from __future__ import annotations

import base64
import io
import json
import socket
import time

import paramiko

from models import AuthMode, RemoteConfigProbe, TransportType, VlessServerConfig


class DeploymentError(RuntimeError):
    pass


class VlessDeployer:
    XRAY_VERSION = "26.6.1"

    def __init__(self, log_callback):
        self.log_callback = log_callback
        self.client: paramiko.SSHClient | None = None

    def log(self, message: str) -> None:
        self.log_callback(message)

    def connect(self, config: VlessServerConfig) -> None:
        self.log(f"SSH connect {config.host}:{config.ssh_port}")
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        connect_kwargs = {
            "hostname": config.host.strip(),
            "port": config.ssh_port,
            "username": config.ssh_username.strip(),
            "timeout": 15,
            "banner_timeout": 15,
            "auth_timeout": 15,
        }

        if config.auth_mode == AuthMode.PASSWORD:
            connect_kwargs["password"] = config.ssh_password
        else:
            key_data = config.ssh_private_key.strip()
            if not key_data and config.ssh_key_path.strip():
                with open(config.ssh_key_path.strip(), "r", encoding="utf-8") as fh:
                    key_data = fh.read()
            if not key_data:
                raise DeploymentError("Пустой SSH ключ")
            pkey = self._load_private_key(key_data)
            connect_kwargs["pkey"] = pkey

        client.connect(**connect_kwargs)
        self.client = client
        self.log("SSH подключение установлено")

    def _load_private_key(self, key_data: str):
        key_buffer = io.StringIO(key_data)
        last_error = None
        for key_cls in (
            paramiko.RSAKey,
            paramiko.Ed25519Key,
            paramiko.ECDSAKey,
            paramiko.DSSKey,
        ):
            key_buffer.seek(0)
            try:
                return key_cls.from_private_key(key_buffer)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
        raise DeploymentError(f"Не удалось прочитать SSH ключ: {last_error}")

    def run(self, command: str, check: bool = True) -> str:
        if not self.client:
            raise DeploymentError("SSH не подключен")
        self.log(f"$ {command}")
        stdin, stdout, stderr = self.client.exec_command(command)
        out = stdout.read().decode("utf-8", errors="replace").strip()
        err = stderr.read().decode("utf-8", errors="replace").strip()
        exit_code = stdout.channel.recv_exit_status()
        if out:
            for line in out.splitlines():
                self.log(f"  {line}")
        if err:
            for line in err.splitlines():
                self.log(f"  [stderr] {line}")
        if check and exit_code != 0:
            raise DeploymentError(f"Команда завершилась с кодом {exit_code}: {command}")
        return out

    def upload_text(self, remote_path: str, content: str) -> None:
        if not self.client:
            raise DeploymentError("SSH не подключен")
        payload = base64.b64encode(content.encode("utf-8")).decode("ascii")
        directory = remote_path.rsplit("/", 1)[0]
        cmd = (
            f"mkdir -p {self._q(directory)} && "
            f"printf %s {self._q(payload)} | base64 -d > {self._q(remote_path)}"
        )
        self.run(cmd)

    @staticmethod
    def _q(value: str) -> str:
        return "'" + value.replace("'", "'\"'\"'") + "'"

    def deploy(self, config: VlessServerConfig) -> tuple[str, str]:
        try:
            self.connect(config)
            self.prepare_system()
            self.install_xray()
            self.prepare_certificates(config)
            self.upload_configuration(config)
            self.start_service(config)
            self.verify(config)
            return config.client_json_string(), config.client_uri()
        finally:
            self.close()

    def probe_remote_config(self, config: VlessServerConfig) -> RemoteConfigProbe:
        try:
            self.connect(config)
            return self._probe_remote_config_connected(config)
        finally:
            self.close()

    def delete_remote_config(self, config: VlessServerConfig) -> None:
        try:
            self.connect(config)
            self.log("VLESS DISCONNECTED")
            self.run("systemctl stop vless-xray.service || true", check=False)
            self.run("systemctl disable vless-xray.service || true", check=False)
            self.run("rm -f /etc/systemd/system/vless-xray.service", check=False)
            self.run("rm -f /usr/local/etc/xray/config.json", check=False)
            self.run("rm -f /var/log/xray/access.log /var/log/xray/error.log", check=False)
            if not config.use_lets_encrypt:
                self.run("rm -f /etc/xray/selfsigned.crt /etc/xray/selfsigned.key", check=False)
            self.run("systemctl daemon-reload", check=False)
            self.log("VLESS CONFIG REMOVED")
        finally:
            self.close()

    def _probe_remote_config_connected(self, config: VlessServerConfig) -> RemoteConfigProbe:
        raw = self.run("cat /usr/local/etc/xray/config.json 2>/dev/null || true", check=False)
        if not raw.strip():
            return RemoteConfigProbe(note=f"На порту {config.listen_port} конфиг не найден. Будет создан новый.")

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise DeploymentError(f"Не удалось разобрать server config.json: {exc}") from exc

        for inbound in parsed.get("inbounds", []):
            if int(inbound.get("port", 0)) != config.listen_port:
                continue
            if inbound.get("protocol") != "vless":
                continue

            stream = inbound.get("streamSettings", {})
            transport_value = stream.get("network", TransportType.TCP.value)
            try:
                transport = TransportType(transport_value)
            except ValueError:
                transport = TransportType.TCP

            clients = inbound.get("settings", {}).get("clients", [])
            uuid = ""
            if clients:
                uuid = str(clients[0].get("id", "")).strip()

            ws_path = stream.get("wsSettings", {}).get("path", "/vless") or "/vless"
            certs = stream.get("tlsSettings", {}).get("certificates", [])
            cert_file = certs[0].get("certificateFile", "") if certs else ""
            key_file = certs[0].get("keyFile", "") if certs else ""
            use_lets_encrypt = "/etc/letsencrypt/live/" in cert_file or "/etc/letsencrypt/live/" in key_file
            server_name = ""
            if use_lets_encrypt:
                parts = cert_file.split("/")
                if "live" in parts:
                    live_index = parts.index("live")
                    if live_index + 1 < len(parts):
                        server_name = parts[live_index + 1]

            return RemoteConfigProbe(
                exists=True,
                transport=transport,
                uuid=uuid,
                ws_path=ws_path,
                use_lets_encrypt=use_lets_encrypt,
                server_name=server_name,
                note=f"Найден существующий VLESS конфиг на порту {config.listen_port}. Данные подтянуты с сервера.",
            )

        return RemoteConfigProbe(note=f"На порту {config.listen_port} VLESS конфиг не найден. Будет создан новый.")

    def prepare_system(self) -> None:
        self.run(
            "apt-get update -qq && apt-get install -y -qq "
            "curl unzip openssl certbot ca-certificates"
        )
        self.run("mkdir -p /usr/local/share/xray /usr/local/etc/xray /var/log/xray /etc/xray")

    def install_xray(self) -> None:
        cmd = (
            "arch=\"$(uname -m)\" && "
            "case \"$arch\" in "
            "x86_64) xr_arch='64' ;; "
            "aarch64) xr_arch='arm64-v8a' ;; "
            "*) echo Unsupported arch: $arch; exit 1 ;; "
            "esac && "
            "tmpdir=\"$(mktemp -d)\" && cd \"$tmpdir\" && "
            f"curl -fsSLo xray.zip https://github.com/XTLS/Xray-core/releases/download/v{self.XRAY_VERSION}/Xray-linux-${{xr_arch}}.zip && "
            "unzip -o xray.zip >/dev/null && "
            "install -m 755 xray /usr/local/bin/xray && "
            "install -m 644 geoip.dat /usr/local/share/xray/geoip.dat && "
            "install -m 644 geosite.dat /usr/local/share/xray/geosite.dat && "
            "cd / && rm -rf \"$tmpdir\""
        )
        self.run(cmd)

    def prepare_certificates(self, config: VlessServerConfig) -> None:
        if config.use_lets_encrypt:
            if not config.server_name.strip() or not config.email.strip():
                raise DeploymentError("Для Let's Encrypt нужны домен и email")
            self.log("VLESS TLS START")
            self.run("fuser -k 80/tcp 2>/dev/null || true", check=False)
            self.run(
                "certbot certonly --non-interactive --standalone --agree-tos "
                f"-m {config.email.strip()} -d {config.server_name.strip()}"
            )
        else:
            self.log("VLESS TLS START")
            subject = config.effective_sni or config.host.strip()
            self.run(
                "openssl req -x509 -nodes -newkey rsa:2048 "
                "-keyout /etc/xray/selfsigned.key "
                "-out /etc/xray/selfsigned.crt "
                f"-days 3650 -subj '/CN={subject}'"
            )

    def upload_configuration(self, config: VlessServerConfig) -> None:
        self.upload_text("/usr/local/etc/xray/config.json", config.xray_config())
        service_text = (
            "[Unit]\n"
            "Description=VLESS Xray Service\n"
            "After=network.target\n\n"
            "[Service]\n"
            "Type=simple\n"
            "ExecStart=/usr/local/bin/xray run -config /usr/local/etc/xray/config.json\n"
            "Restart=always\n"
            "RestartSec=3\n"
            "LimitNOFILE=1048576\n\n"
            "[Install]\n"
            "WantedBy=multi-user.target\n"
        )
        self.upload_text("/etc/systemd/system/vless-xray.service", service_text)

    def start_service(self, config: VlessServerConfig) -> None:
        self.run("systemctl daemon-reload")
        self.run("systemctl enable vless-xray.service")
        self.run("systemctl restart vless-xray.service")
        self.log("VLESS CONNECT")

    def verify(self, config: VlessServerConfig) -> None:
        status = self.run("systemctl is-active vless-xray.service || true", check=False).strip()
        if status != "active":
            journal = self.run(
                "journalctl -u vless-xray.service --no-pager -n 80 2>/dev/null || true",
                check=False,
            )
            raise DeploymentError(f"Xray не стартовал\n{journal}")

        port_check = self.run(f"ss -ltnp | grep :{config.listen_port} || true", check=False)
        if not port_check.strip():
            raise DeploymentError(f"Порт {config.listen_port} не слушается")

        self.log("VLESS HANDSHAKE OK")

    def close(self) -> None:
        if self.client:
            self.client.close()
            self.client = None
