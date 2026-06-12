from __future__ import annotations

import ipaddress
import json
import secrets
import uuid as uuid_lib
from dataclasses import dataclass
from enum import Enum
from urllib.parse import urlencode, quote


class TransportType(str, Enum):
    TCP = "tcp"
    WS = "ws"


class AuthMode(str, Enum):
    PASSWORD = "password"
    PRIVATE_KEY = "private_key"


@dataclass
class RemoteConfigProbe:
    exists: bool = False
    transport: TransportType = TransportType.TCP
    uuid: str = ""
    ws_path: str = "/vless"
    use_lets_encrypt: bool = False
    server_name: str = ""
    email: str = ""
    note: str = ""


@dataclass
class VlessServerConfig:
    host: str = ""
    ssh_port: int = 22
    ssh_username: str = "root"
    ssh_password: str = ""
    ssh_private_key: str = ""
    ssh_key_path: str = ""
    auth_mode: AuthMode = AuthMode.PASSWORD
    listen_port: int = 443
    transport: TransportType = TransportType.TCP
    uuid: str = ""
    server_name: str = ""
    email: str = ""
    ws_path: str = "/vless"
    use_lets_encrypt: bool = False
    allow_insecure: bool = True
    profile_name: str = "VLESS Server"

    def __post_init__(self) -> None:
        if not self.uuid:
            self.uuid = str(uuid_lib.uuid4())

    @property
    def effective_server(self) -> str:
        return self.host.strip() or self.server_name.strip()

    @staticmethod
    def default_sni_for_host(host: str) -> str:
        host = host.strip()
        if not host:
            return ""
        try:
            ipaddress.ip_address(host)
            return "yandex.ru"
        except ValueError:
            return host

    @property
    def effective_sni(self) -> str:
        return self.server_name.strip() or self.default_sni_for_host(self.host)

    @property
    def normalized_ws_path(self) -> str:
        path = self.ws_path.strip() or "/vless"
        return path if path.startswith("/") else f"/{path}"

    @property
    def certificate_file(self) -> str:
        if self.use_lets_encrypt and self.server_name.strip():
            return f"/etc/letsencrypt/live/{self.server_name.strip()}/fullchain.pem"
        return "/etc/xray/selfsigned.crt"

    @property
    def key_file(self) -> str:
        if self.use_lets_encrypt and self.server_name.strip():
            return f"/etc/letsencrypt/live/{self.server_name.strip()}/privkey.pem"
        return "/etc/xray/selfsigned.key"

    def client_json(self) -> dict:
        return {
            "server": self.effective_server,
            "port": self.listen_port,
            "uuid": self.uuid,
            "tls": True,
            "sni": self.effective_sni,
            "transport": self.transport.value,
            "wsPath": self.normalized_ws_path if self.transport == TransportType.WS else "",
            "insecure": self.allow_insecure,
            "remarks": self.profile_name.strip(),
        }

    def client_json_string(self) -> str:
        return json.dumps(self.client_json(), ensure_ascii=False, indent=2)

    def to_dict(self) -> dict:
        return {
            "host": self.host,
            "ssh_port": self.ssh_port,
            "ssh_username": self.ssh_username,
            "ssh_password": self.ssh_password,
            "ssh_private_key": self.ssh_private_key,
            "ssh_key_path": self.ssh_key_path,
            "auth_mode": self.auth_mode.value,
            "listen_port": self.listen_port,
            "transport": self.transport.value,
            "uuid": self.uuid,
            "server_name": self.server_name,
            "email": self.email,
            "ws_path": self.ws_path,
            "use_lets_encrypt": self.use_lets_encrypt,
            "allow_insecure": self.allow_insecure,
            "profile_name": self.profile_name,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "VlessServerConfig":
        return cls(
            host=data.get("host", ""),
            ssh_port=int(data.get("ssh_port", 22)),
            ssh_username=data.get("ssh_username", "root"),
            ssh_password=data.get("ssh_password", ""),
            ssh_private_key=data.get("ssh_private_key", ""),
            ssh_key_path=data.get("ssh_key_path", ""),
            auth_mode=AuthMode(data.get("auth_mode", AuthMode.PASSWORD.value)),
            listen_port=int(data.get("listen_port", 443)),
            transport=TransportType(data.get("transport", TransportType.TCP.value)),
            uuid=data.get("uuid", ""),
            server_name=data.get("server_name", ""),
            email=data.get("email", ""),
            ws_path=data.get("ws_path", "/vless"),
            use_lets_encrypt=bool(data.get("use_lets_encrypt", False)),
            allow_insecure=bool(data.get("allow_insecure", True)),
            profile_name=data.get("profile_name", "VLESS Server"),
        )

    def client_uri(self) -> str:
        params = {
            "encryption": "none",
            "security": "tls",
            "type": self.transport.value,
            "sni": self.effective_sni,
        }
        if self.transport == TransportType.WS:
            params["path"] = self.normalized_ws_path
            params["host"] = self.effective_sni
        if self.allow_insecure:
            params["allowInsecure"] = "1"
        fragment = quote(self.profile_name.strip() or "VLESS")
        return (
            f"vless://{self.uuid}@{self.effective_server}:{self.listen_port}"
            f"?{urlencode(params)}#{fragment}"
        )

    def xray_config(self) -> str:
        stream_settings = {
            "network": self.transport.value,
            "security": "tls",
            "tlsSettings": {
                "certificates": [
                    {
                        "certificateFile": self.certificate_file,
                        "keyFile": self.key_file,
                    }
                ]
            },
        }
        if self.transport == TransportType.WS:
            stream_settings["wsSettings"] = {"path": self.normalized_ws_path}

        config = {
            "log": {
                "loglevel": "warning",
                "access": "/var/log/xray/access.log",
                "error": "/var/log/xray/error.log",
            },
            "inbounds": [
                {
                    "listen": "0.0.0.0",
                    "port": self.listen_port,
                    "protocol": "vless",
                    "settings": {
                        "clients": [{"id": self.uuid}],
                        "decryption": "none",
                    },
                    "streamSettings": stream_settings,
                }
            ],
            "outbounds": [{"protocol": "freedom"}],
        }
        return json.dumps(config, ensure_ascii=False, indent=2)

    @staticmethod
    def random_password(length: int = 20) -> str:
        alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
        return "".join(alphabet[secrets.randbelow(len(alphabet))] for _ in range(length))
