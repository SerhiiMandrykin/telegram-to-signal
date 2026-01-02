# Telegram to Signal

Forward messages from Telegram chats/channels to Signal groups.

## Setup

1. Copy `.env.example` to `.env` and fill in your credentials
2. Copy `config.json.example` to `config.json`

## Sign in to Signal

**Important:** It is recommended to use a separate Signal account instead of your personal one. This account will act like a bot, so you will receive messages from another account and it will feel more natural.

Link Signal CLI to your Signal account:

```bash
# Docker
docker compose run --rm -it signal-cli --config /var/lib/signal-cli link -n "Telegram Bridge"

# Podman
podman compose run --rm signal-cli --config /var/lib/signal-cli link -n "Telegram Bridge"
```

This will display a link like `sgnl://linkdevice?uuid=...&pub_key=...`. Generate a QR code from this link (e.g., using https://qr.io or any QR generator) and scan it with your Signal app (Settings > Linked Devices > Link New Device).

## Sign in to Telegram

Before running the main app, you need to authenticate with Telegram:

```bash
# Docker
docker compose run --rm -it telegram-app python signin.py

# Podman
podman compose run --rm telegram-app python signin.py
```

This will prompt for your phone number, verification code, and 2FA password (if enabled).

## Run

```bash
# Docker
docker compose up -d

# Podman
podman compose up -d
```

<details>
<summary>Podman: Enable automatic container restart</summary>

Podman is daemonless, so `restart: always` requires additional setup:

```bash
# Enable the user-level restart service
systemctl --user enable --now podman-restart.service

# Allow services to run when logged out
sudo loginctl enable-linger $USER
```

Without this, containers won't restart automatically after exiting or rebooting.

</details>
