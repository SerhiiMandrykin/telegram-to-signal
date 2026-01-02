import json

_config_data = None
_signal_to_telegram: dict[str, tuple[str, bool]] = {}


def _build_reverse_mapping():
    """Build reverse mapping from Signal group ID to (Telegram chat ID, is_channel)."""
    global _signal_to_telegram
    _signal_to_telegram = {}

    for chat_id, group_id in _config_data.get('chats', {}).items():
        _signal_to_telegram[group_id] = (chat_id, False)

    for chat_id, group_id in _config_data.get('channels', {}).items():
        _signal_to_telegram[group_id] = (chat_id, True)


def get_config():
    global _config_data

    if _config_data is not None:
        return _config_data

    with open('config.json', 'r') as config_file:
        _config_data = json.loads(config_file.read())

    _build_reverse_mapping()
    return _config_data


def save_config():
    global _config_data
    with open('config.json', 'w') as config_file:
        json.dump(_config_data, config_file, indent=4)
    _build_reverse_mapping()


def get_telegram_chat_id(signal_group_id: str) -> tuple[str, bool] | None:
    """
    Get Telegram chat ID for a Signal group ID (reverse lookup).

    Returns: tuple of (chat_id, is_channel) or None if not found.
    """
    return _signal_to_telegram.get(signal_group_id)
