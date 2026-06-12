# VLESS Server Configurator

Desktop GUI на Python для:

- сбора VLESS TCP/TLS или WS/TLS конфига
- деплоя Xray на VPS по SSH
- генерации клиентского JSON
- генерации `vless://` URI
- показа QR

## Запуск

```powershell
cd D:\VPN\tmp\vless-server-configurator
python -m pip install -r requirements.txt
python app.py
```

## Что умеет

- SSH по паролю
- SSH по приватному ключу
- TLS через Let's Encrypt
- self-signed TLS для тестов
- VLESS TCP + TLS
- VLESS WS + TLS

## Что делает на сервере

- ставит `curl unzip openssl certbot ca-certificates`
- скачивает Xray `v26.6.1`
- кладет `config.json` в `/usr/local/etc/xray/config.json`
- создает `vless-xray.service`
- включает и перезапускает сервис
- проверяет `systemctl is-active`
- проверяет, что порт слушается

## Замечания

- Для Let's Encrypt домен должен указывать на VPS и 80/tcp должен быть доступен.
- UUID и пользовательские payload в лог не пишутся.
- Для self-signed клиента обычно нужен `insecure=true`.
