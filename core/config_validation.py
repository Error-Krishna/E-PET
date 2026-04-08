from copy import deepcopy


DEFAULT_CONFIG = {
    "hardware": {
        "mode": "simulator",
        "debug": False,
    },
    "plugins": {
        "enabled": ["emotion", "sound", "idle", "os_bridge"],
    },
    "simulator": {
        "fps": 10,
    },
    "personality": {
        "pet_name": "Mochi",
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
    "event_bus": {
        "ordered": True,
        "log_events": False,
    },
    "os_bridge": {
        "enabled": True,
        "delay_between_actions": 0.3,
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
        "mode": "auto",
        "groq_api_key": "",
        "groq_model": "llama-3.1-8b-instant",
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
    ordered = config.get("event_bus", {}).get("ordered", True)
    if isinstance(ordered, str):
        ordered = ordered.strip().lower() in {"1", "true", "yes", "on"}
    config.setdefault("event_bus", {})
    config["event_bus"]["ordered"] = bool(ordered)
    log_events = config["event_bus"].get("log_events", False)
    if isinstance(log_events, str):
        log_events = log_events.strip().lower() in {"1", "true", "yes", "on"}
    config["event_bus"]["log_events"] = bool(log_events)
    config["personality"]["pet_name"] = str(config["personality"].get("pet_name", "Mochi")).strip() or "Mochi"
    ai_mode = str(config["ai"].get("mode", "offline")).strip().lower()
    if ai_mode == "local":
        ai_mode = "offline"
    if ai_mode not in {"offline", "online", "auto"}:
        ai_mode = "auto"
    config["ai"]["mode"] = ai_mode
    config["ai"]["groq_api_key"] = str(config["ai"].get("groq_api_key", "")).strip()
    groq_model = str(config["ai"].get("groq_model", "llama-3.1-8b-instant")).strip()
    config["ai"]["groq_model"] = groq_model or "llama-3.1-8b-instant"

    return config
