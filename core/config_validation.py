import logging
from copy import deepcopy
from pathlib import Path

from core.memory import DEFAULT_MEMORY_FILENAME

logger = logging.getLogger(__name__)


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
        "assistant_mode": False,
        "curiosity": 0.5,
        "energy": 0.6,
        "sociability": 0.5,
        # Derived by the memory manager; not a user-editable config knob.
        "bond_level": 0.0,
        "name": "",
    },
    "idle": {
        "bored_after": 120,
        "sleepy_after": 300,
    },
    "logging": {
        "level": "INFO",
        "file": "epet.log",
    },
    "event_bus": {
        "ordered": True,
        "log_events": False,
    },
    "os_bridge": {
        "enabled": True,
        "delay_between_actions": 0.3,
        "max_retries": 2,
        "retry_delay": 0.15,
        "continue_on_failure": False,
        "verify_after_actions": False,
        "verification_delay": 0.75,
    },
    "voice": {
        "enabled": True,
        "wake_word": "hey pip",
        "wake_mode": "auto",
        "wake_keyword": "computer",
        "stt_backend": "auto",
        "tts_backend": "piper",
        "porcupine_access_key": "",
        "porcupine_keyword_path": "",
        "piper_path": "piper",
        "tts_model": "",
        "whisper_model": "base",
        "wake_whisper_model": "tiny",
        "stt_language": "en",
        "stt_beam_size": 5,
        "stt_best_of": 1,
        "stt_retry_on_low_confidence": True,
        "stt_retry_beam_size": 6,
        "stt_low_confidence_logprob": -1.05,
        "stt_no_speech_threshold": 0.65,
        "stt_apply_gain": True,
        "stt_target_rms": 3800.0,
        "stt_max_gain": 4.0,
        "stt_reject_non_latin_when_english": True,
        "stt_min_latin_ratio": 0.45,
        "echo_guard_seconds": 2.2,
        "echo_similarity_threshold": 0.72,
        "echo_min_chars": 8,
        "echo_retry_limit": 2,
        "echo_retry_backoff_seconds": 0.35,
        "wake_listen_seconds": 2,
        "wake_max_phrase_seconds": 2.8,
        "wake_silence_seconds": 0.55,
        "wake_min_speech_seconds": 0.2,
        "wake_vad_sensitivity": 0.68,
        "wake_language": "en",
        "wake_initial_prompt": "",
        "follow_up_listen_seconds": 2,
        "conversation_window_seconds": 8.0,
        "silence_threshold_seconds": 1.4,
        "min_speech_duration_seconds": 0.35,
        "vad_sensitivity": 0.65,
        "wake_check_interval": 0.3,
        "wake_cooldown_seconds": 4.0,
        "mic_lock_timeout": 5.0,
        "interrupt_on_new_speech": True,
    },
    "ai": {
        "enabled": True,
        "mode": "auto",
        "groq_api_key": "",
        "groq_model": "llama-3.1-8b-instant",
        "groq_max_tokens": 256,
        "ollama_host": "http://localhost:11434",
        "ollama_model": "phi3:mini",
        "ollama_keep_alive": "10m",
        "ollama_temperature": 0.7,
        "ollama_num_ctx": 1024,
        "ollama_num_predict": 96,
        "request_timeout": 60,
    },
    "memory": {
        "max_history": 20,
        "persist_history": True,
        "extract_facts": True,
        "db_path": DEFAULT_MEMORY_FILENAME,
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
    voice_raw = (raw_config or {}).get("voice", {}) if isinstance(raw_config, dict) else {}

    enabled_plugins = config.get("plugins", {}).get("enabled", [])
    if not isinstance(enabled_plugins, list):
        raise ValueError("config.plugins.enabled must be a list")
    config["plugins"]["enabled"] = list(dict.fromkeys(enabled_plugins))

    logging_level = str(config.get("logging", {}).get("level", "INFO")).upper()
    if logging_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        logging_level = "INFO"
    config["logging"]["level"] = logging_level
    log_file = str(config.get("logging", {}).get("file", "epet.log")).strip()
    config["logging"]["file"] = log_file or "epet.log"

    if config["idle"]["bored_after"] < 0 or config["idle"]["sleepy_after"] < 0:
        raise ValueError("idle timeouts must be >= 0")
    if config["idle"]["sleepy_after"] < config["idle"]["bored_after"]:
        config["idle"]["sleepy_after"] = config["idle"]["bored_after"]

    config["voice"]["wake_listen_seconds"] = max(1, int(config["voice"]["wake_listen_seconds"]))
    config["voice"]["follow_up_listen_seconds"] = max(1, int(config["voice"].get("follow_up_listen_seconds", config["voice"]["wake_listen_seconds"])))
    if "conversation_window_seconds" in voice_raw:
        config["voice"]["conversation_window_seconds"] = max(
            1.0,
            float(config["voice"].get("conversation_window_seconds", 8.0)),
        )
    else:
        config["voice"]["conversation_window_seconds"] = max(
            1.0,
            float(config["voice"].get("follow_up_listen_seconds", 8.0)),
        )
    config["voice"]["silence_threshold_seconds"] = max(
        0.5,
        float(config["voice"].get("silence_threshold_seconds", 1.4)),
    )
    config["voice"]["min_speech_duration_seconds"] = max(
        0.1,
        float(config["voice"].get("min_speech_duration_seconds", 0.35)),
    )
    config["voice"]["vad_sensitivity"] = min(
        1.0,
        max(0.0, float(config["voice"].get("vad_sensitivity", 0.65))),
    )
    config["voice"]["wake_check_interval"] = max(0.1, float(config["voice"]["wake_check_interval"]))
    config["voice"]["wake_cooldown_seconds"] = max(0.5, float(config["voice"]["wake_cooldown_seconds"]))
    config["voice"]["mic_lock_timeout"] = max(0.5, float(config["voice"].get("mic_lock_timeout", 5.0)))
    config["voice"]["stt_backend"] = str(config["voice"].get("stt_backend", "auto")).strip() or "auto"
    config["voice"]["tts_backend"] = str(config["voice"].get("tts_backend", "piper")).strip() or "piper"
    stt_language = str(config["voice"].get("stt_language", "en")).strip().lower()
    if stt_language in {"", "any"}:
        stt_language = "auto"
    config["voice"]["stt_language"] = stt_language
    config["voice"]["stt_beam_size"] = max(1, int(config["voice"].get("stt_beam_size", 5)))
    config["voice"]["stt_best_of"] = max(1, int(config["voice"].get("stt_best_of", 1)))
    config["voice"]["stt_retry_beam_size"] = max(
        config["voice"]["stt_beam_size"],
        int(config["voice"].get("stt_retry_beam_size", 6)),
    )
    config["voice"]["stt_low_confidence_logprob"] = float(
        config["voice"].get("stt_low_confidence_logprob", -1.05)
    )
    config["voice"]["stt_no_speech_threshold"] = min(
        1.0,
        max(0.0, float(config["voice"].get("stt_no_speech_threshold", 0.65))),
    )
    config["voice"]["stt_target_rms"] = max(500.0, float(config["voice"].get("stt_target_rms", 3800.0)))
    config["voice"]["stt_max_gain"] = max(1.0, float(config["voice"].get("stt_max_gain", 4.0)))
    config["voice"]["stt_min_latin_ratio"] = min(
        1.0,
        max(0.0, float(config["voice"].get("stt_min_latin_ratio", 0.45))),
    )
    config["voice"]["echo_guard_seconds"] = max(
        0.0,
        float(config["voice"].get("echo_guard_seconds", 2.2)),
    )
    config["voice"]["echo_similarity_threshold"] = min(
        1.0,
        max(0.5, float(config["voice"].get("echo_similarity_threshold", 0.72))),
    )
    config["voice"]["echo_min_chars"] = max(4, int(config["voice"].get("echo_min_chars", 8)))
    config["voice"]["echo_retry_limit"] = max(0, int(config["voice"].get("echo_retry_limit", 2)))
    config["voice"]["echo_retry_backoff_seconds"] = max(
        0.0,
        float(config["voice"].get("echo_retry_backoff_seconds", 0.35)),
    )
    config["voice"]["wake_max_phrase_seconds"] = max(
        float(config["voice"]["wake_listen_seconds"]),
        float(config["voice"].get("wake_max_phrase_seconds", 2.8)),
    )
    config["voice"]["wake_silence_seconds"] = max(
        0.2,
        float(config["voice"].get("wake_silence_seconds", 0.55)),
    )
    config["voice"]["wake_min_speech_seconds"] = max(
        0.1,
        float(config["voice"].get("wake_min_speech_seconds", 0.2)),
    )
    config["voice"]["wake_vad_sensitivity"] = min(
        1.0,
        max(0.0, float(config["voice"].get("wake_vad_sensitivity", 0.68))),
    )
    wake_language = str(
        config["voice"].get("wake_language", config["voice"].get("stt_language", "en"))
    ).strip().lower()
    if wake_language in {"", "any"}:
        wake_language = "auto"
    config["voice"]["wake_language"] = wake_language
    config["voice"]["wake_initial_prompt"] = str(config["voice"].get("wake_initial_prompt", "")).strip()
    for key in (
        "stt_retry_on_low_confidence",
        "stt_apply_gain",
        "stt_reject_non_latin_when_english",
    ):
        raw_value = config["voice"].get(key, True)
        if isinstance(raw_value, str):
            raw_value = raw_value.strip().lower() in {"1", "true", "yes", "on"}
        config["voice"][key] = bool(raw_value)
    config["ai"]["request_timeout"] = max(5, int(config["ai"]["request_timeout"]))
    config["simulator"]["fps"] = max(1, int(config["simulator"].get("fps", 10)))
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
    assistant_mode = config["personality"].get("assistant_mode", False)
    if isinstance(assistant_mode, str):
        assistant_mode = assistant_mode.strip().lower() in {"1", "true", "yes", "on"}
    config["personality"]["assistant_mode"] = bool(assistant_mode)
    ai_mode = str(config["ai"].get("mode", "offline")).strip().lower()
    if ai_mode == "local":
        ai_mode = "offline"
    if ai_mode not in {"offline", "online", "auto"}:
        ai_mode = "auto"
    config["ai"]["mode"] = ai_mode
    config["ai"]["groq_api_key"] = str(config["ai"].get("groq_api_key", "")).strip()
    groq_model = str(config["ai"].get("groq_model", "llama-3.1-8b-instant")).strip()
    config["ai"]["groq_model"] = groq_model or "llama-3.1-8b-instant"
    config["ai"]["groq_max_tokens"] = max(16, int(config["ai"].get("groq_max_tokens", 256)))
    ollama_host = str(config["ai"].get("ollama_host", "http://localhost:11434")).strip().rstrip("/")
    if not ollama_host:
        ollama_host = "http://localhost:11434"
    if "://" not in ollama_host:
        ollama_host = f"http://{ollama_host}"
    config["ai"]["ollama_host"] = ollama_host
    ollama_model = str(config["ai"].get("ollama_model", "phi3:mini")).strip()
    config["ai"]["ollama_model"] = ollama_model or "phi3:mini"
    ollama_keep_alive = str(config["ai"].get("ollama_keep_alive", "10m")).strip()
    config["ai"]["ollama_keep_alive"] = ollama_keep_alive or "10m"
    config["ai"]["ollama_temperature"] = max(0.0, float(config["ai"].get("ollama_temperature", 0.7)))
    config["ai"]["ollama_num_ctx"] = max(256, int(config["ai"].get("ollama_num_ctx", 1024)))
    config["ai"]["ollama_num_predict"] = max(16, int(config["ai"].get("ollama_num_predict", 96)))
    config["os_bridge"]["delay_between_actions"] = max(0.0, float(config["os_bridge"]["delay_between_actions"]))
    config["os_bridge"]["max_retries"] = max(0, int(config["os_bridge"]["max_retries"]))
    config["os_bridge"]["retry_delay"] = max(0.0, float(config["os_bridge"]["retry_delay"]))
    continue_on_failure = config["os_bridge"].get("continue_on_failure", False)
    if isinstance(continue_on_failure, str):
        continue_on_failure = continue_on_failure.strip().lower() in {"1", "true", "yes", "on"}
    config["os_bridge"]["continue_on_failure"] = bool(continue_on_failure)
    verify_after_actions = config["os_bridge"].get("verify_after_actions", False)
    if isinstance(verify_after_actions, str):
        verify_after_actions = verify_after_actions.strip().lower() in {"1", "true", "yes", "on"}
    config["os_bridge"]["verify_after_actions"] = bool(verify_after_actions)
    config["os_bridge"]["verification_delay"] = max(0.0, float(config["os_bridge"].get("verification_delay", 0.75)))
    config["memory"]["db_path"] = str(config["memory"].get("db_path", DEFAULT_MEMORY_FILENAME)).strip() or DEFAULT_MEMORY_FILENAME

    db_path = Path(config["memory"]["db_path"]).expanduser()
    if db_path != Path(":memory:"):
        try:
            db_path.parent.mkdir(parents=True, exist_ok=True)
            db_path.touch(exist_ok=True)
        except Exception as exc:
            logger.warning("memory.db_path is not writable (%s): %s", db_path, exc)

    return config
