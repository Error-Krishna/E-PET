from copy import deepcopy


DEFAULT_CONFIG = {
    "hardware": {
        "mode": "simulator",
        "debug": False,
    },
    "plugins": {
        "enabled": ["emotion", "sound", "idle"],
    },
    "simulator": {
        "fps": 10,
    },
    "personality": {
        "curiosity": 0.5,
        "energy": 0.6,
        "sociability": 0.5,
        "bond_level": 0.0,
        "name": "krishna",
    },
    "idle": {
        "bored_after": 120,
        "sleepy_after": 300,
    },
    "logging": {
        "level": "INFO",
    },
    "voice": {
        "enabled": True,
        "wake_word": "hey pip",
        "wake_mode": "auto",
        "wake_keyword": "computer",
        "porcupine_access_key": "",
        "porcupine_keyword_path": "",
        "piper_path": "piper",
        "tts_model": "",
        "whisper_model": "tiny",
        "wake_whisper_model": "tiny",
        "record_seconds": 3,
        "wake_listen_seconds": 2,
        "wake_check_interval": 0.3,
        "wake_cooldown_seconds": 4.0,
        "interrupt_on_new_speech": True,
    },
    "ai": {
        "enabled": True,
        "mode": "local",
        "model": "phi3:mini",
        "api_key": "",
        "local_url": "http://localhost:11434/api/generate",
        "online_url": "https://api.openai.com/v1/chat/completions",
        "online_model": "gpt-3.5-turbo",
        "request_timeout": 60,
    },
    "memory": {
        "max_history": 20,
        "persist_history": True,
        "extract_facts": True,
    },
}


def _merge_dicts(base, override):
    merged = deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_dicts(merged[key], value)
        else:
            merged[key] = value
    return merged


def normalize_and_validate_config(raw_config):
    config = _merge_dicts(DEFAULT_CONFIG, raw_config or {})

    enabled_plugins = config.get("plugins", {}).get("enabled", [])
    if not isinstance(enabled_plugins, list):
        raise ValueError("config.plugins.enabled must be a list")
    config["plugins"]["enabled"] = list(dict.fromkeys(enabled_plugins))

    logging_level = str(config.get("logging", {}).get("level", "INFO")).upper()
    if logging_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        logging_level = "INFO"
    config["logging"]["level"] = logging_level

    if config["idle"]["bored_after"] < 0 or config["idle"]["sleepy_after"] < 0:
        raise ValueError("idle timeouts must be >= 0")
    if config["idle"]["sleepy_after"] < config["idle"]["bored_after"]:
        config["idle"]["sleepy_after"] = config["idle"]["bored_after"]

    config["voice"]["record_seconds"] = max(1, int(config["voice"]["record_seconds"]))
    config["voice"]["wake_listen_seconds"] = max(1, int(config["voice"]["wake_listen_seconds"]))
    config["voice"]["wake_check_interval"] = max(0.1, float(config["voice"]["wake_check_interval"]))
    config["voice"]["wake_cooldown_seconds"] = max(0.5, float(config["voice"]["wake_cooldown_seconds"]))
    config["ai"]["request_timeout"] = max(5, int(config["ai"]["request_timeout"]))

    return config
