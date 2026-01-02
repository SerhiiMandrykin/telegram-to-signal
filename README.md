# Telegram to Signal

Forward messages from Telegram chats/channels to Signal groups.

## Features

### Message Forwarding
- **Text messages** with sender attribution in group chats
- **Media support**: photos, videos, voice notes, video notes, and albums
- **Text formatting**: bold, italic, strikethrough, spoiler, and monospace converted to Signal styles
- **Bidirectional sync** (optional): forward Signal messages back to Telegram

### Media Conversion
- Voice notes automatically converted between formats (OGG ↔ M4A) for cross-platform compatibility
- Video notes supported in both directions
- Albums/grouped media sent as single messages with multiple attachments

### Automatic Group Management
- Signal groups created on-demand when first message arrives from unmapped Telegram chat
- Group avatars synced from Telegram
- Configurable group name prefix (e.g., "(Telegram) Chat Name")
- Message expiration policy for created groups

### Configuration Options
- Enable/disable channel forwarding
- Enable/disable read receipts for messages and channels separately
- Configurable message retention period
- Optional bidirectional messaging

## Architecture

The bridge runs two services in Docker/Podman containers:
1. **Signal CLI** - Provides JSON-RPC API and Server-Sent Events (SSE) for Signal messaging
2. **Telegram App** - Python application using Telethon to connect to Telegram

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
