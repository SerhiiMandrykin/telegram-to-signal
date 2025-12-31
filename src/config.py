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
