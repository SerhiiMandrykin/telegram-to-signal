import json

_config_loaded = False


def get_config():
    global _config_loaded

    if _config_loaded:
        return _config_loaded

    with open('config.json', 'r') as config_file:
        _config_loaded = json.loads(config_file.read())

    return _config_loaded


def save_config():
    global _config_loaded
    with open('config.json', 'w') as config_file:
        json.dump(_config_loaded, config_file, indent=4)


def get_telegram_chat_id(signal_group_id: str) -> tuple[str, bool] | None:
    """
    Get Telegram chat ID for a Signal group ID (reverse lookup).

    Returns: tuple of (chat_id, is_channel) or None if not found.
    """
    config = get_config()

    # Search in chats
    for chat_id, group_id in config.get('chats', {}).items():
        if group_id == signal_group_id:
            return (chat_id, False)

    # Search in channels
    for chat_id, group_id in config.get('channels', {}).items():
        if group_id == signal_group_id:
            return (chat_id, True)

    return None
