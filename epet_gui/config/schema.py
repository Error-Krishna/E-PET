from __future__ import annotations

from core.config_validation import DEFAULT_CONFIG

PLUGIN_NAMES = ["emotion", "sound", "idle", "os_bridge", "voice", "ai"]


def _default(*path):
    value = DEFAULT_CONFIG
    for key in path:
        value = value[key]
    return value


CONFIG_SCHEMA = {
    "General": {
        "description": "Core runtime and assistant identity settings. Bond level is derived and intentionally omitted.",
        "fields": [
            {"path": ("personality", "pet_name"), "label": "Assistant Name", "type": "str", "default": _default("personality", "pet_name")},
            {"path": ("personality", "assistant_mode"), "label": "Assistant Mode", "type": "bool", "default": _default("personality", "assistant_mode")},
            {"path": ("personality", "name"), "label": "Owner Name", "type": "str", "default": _default("personality", "name")},
            {"path": ("personality", "curiosity"), "label": "Curiosity", "type": "float", "default": _default("personality", "curiosity"), "min": 0.0, "max": 1.0, "step": 0.05},
            {"path": ("personality", "energy"), "label": "Energy", "type": "float", "default": _default("personality", "energy"), "min": 0.0, "max": 1.0, "step": 0.05},
            {"path": ("personality", "sociability"), "label": "Sociability", "type": "float", "default": _default("personality", "sociability"), "min": 0.0, "max": 1.0, "step": 0.05},
            {"path": ("hardware", "mode"), "label": "Hardware Mode", "type": "enum", "choices": ["simulator", "normal", "headless"], "default": _default("hardware", "mode")},
            {"path": ("hardware", "debug"), "label": "Hardware Debug", "type": "bool", "default": _default("hardware", "debug")},
            {"path": ("logging", "level"), "label": "Log Level", "type": "enum", "choices": ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], "default": _default("logging", "level")},
            {"path": ("logging", "file"), "label": "Log File", "type": "path", "default": _default("logging", "file")},
        ],
    },
    "Simulator": {
        "description": "Local face renderer pacing when the simulator is active.",
        "fields": [
            {"path": ("simulator", "fps"), "label": "Render FPS", "type": "int", "default": _default("simulator", "fps"), "min": 1, "max": 120, "step": 1},
        ],
    },
    "Idle": {
        "description": "Automatic mood transitions while the pet is idle.",
        "fields": [
            {"path": ("idle", "bored_after"), "label": "Bored After (seconds)", "type": "int", "default": _default("idle", "bored_after"), "min": 10, "max": 3600, "step": 10},
            {"path": ("idle", "sleepy_after"), "label": "Sleepy After (seconds)", "type": "int", "default": _default("idle", "sleepy_after"), "min": 30, "max": 7200, "step": 10},
        ],
    },
    "AI": {
        "description": "Online Groq and offline Ollama controls.",
        "fields": [
            {"path": ("ai", "enabled"), "label": "Enable AI", "type": "bool", "default": _default("ai", "enabled")},
            {"path": ("ai", "mode"), "label": "AI Mode", "type": "enum", "choices": ["auto", "online", "offline"], "default": _default("ai", "mode")},
            {"path": ("ai", "groq_api_key"), "label": "Groq API Key", "type": "secret", "default": _default("ai", "groq_api_key")},
            {"path": ("ai", "groq_model"), "label": "Groq Model", "type": "str", "default": _default("ai", "groq_model")},
            {"path": ("ai", "ollama_host"), "label": "Ollama Host", "type": "str", "default": _default("ai", "ollama_host")},
            {"path": ("ai", "ollama_model"), "label": "Ollama Model", "type": "str", "default": _default("ai", "ollama_model")},
            {"path": ("ai", "ollama_keep_alive"), "label": "Ollama Keep Alive", "type": "str", "default": _default("ai", "ollama_keep_alive")},
            {"path": ("ai", "ollama_temperature"), "label": "Ollama Temperature", "type": "float", "default": _default("ai", "ollama_temperature"), "min": 0.0, "max": 2.0, "step": 0.05},
            {"path": ("ai", "ollama_num_ctx"), "label": "Ollama Context", "type": "int", "default": _default("ai", "ollama_num_ctx"), "min": 256, "max": 8192, "step": 128},
            {"path": ("ai", "ollama_num_predict"), "label": "Ollama Output Tokens", "type": "int", "default": _default("ai", "ollama_num_predict"), "min": 16, "max": 1024, "step": 16},
            {"path": ("ai", "request_timeout"), "label": "Request Timeout", "type": "int", "default": _default("ai", "request_timeout"), "min": 5, "max": 300, "step": 5},
        ],
    },
    "Voice": {
        "description": "Wake word, STT, and TTS behavior. This is a subset of the full config editor.",
        "fields": [
            {"path": ("voice", "enabled"), "label": "Enable Voice", "type": "bool", "default": _default("voice", "enabled")},
            {"path": ("voice", "wake_word"), "label": "Wake Word", "type": "str", "default": _default("voice", "wake_word")},
            {"path": ("voice", "wake_mode"), "label": "Wake Mode", "type": "enum", "choices": ["auto", "whisper", "porcupine", "keyboard"], "default": _default("voice", "wake_mode")},
            {"path": ("voice", "stt_backend"), "label": "STT Backend", "type": "enum", "choices": ["auto", "whisper", "faster-whisper"], "default": _default("voice", "stt_backend")},
            {"path": ("voice", "tts_backend"), "label": "TTS Backend", "type": "enum", "choices": ["piper", "pyttsx3", "none"], "default": _default("voice", "tts_backend")},
            {"path": ("voice", "wake_keyword"), "label": "Wake Keyword", "type": "str", "default": _default("voice", "wake_keyword")},
            {"path": ("voice", "porcupine_access_key"), "label": "Porcupine Access Key", "type": "secret", "default": _default("voice", "porcupine_access_key")},
            {"path": ("voice", "porcupine_keyword_path"), "label": "Porcupine Keyword Path", "type": "path", "default": _default("voice", "porcupine_keyword_path")},
            {"path": ("voice", "piper_path"), "label": "Piper Path", "type": "str", "default": _default("voice", "piper_path")},
            {"path": ("voice", "tts_model"), "label": "TTS Model", "type": "path", "default": _default("voice", "tts_model")},
            {"path": ("voice", "whisper_model"), "label": "STT Model", "type": "str", "default": _default("voice", "whisper_model")},
            {"path": ("voice", "wake_whisper_model"), "label": "Wake Whisper Model", "type": "str", "default": _default("voice", "wake_whisper_model")},
            {"path": ("voice", "stt_language"), "label": "STT Language", "type": "str", "default": _default("voice", "stt_language")},
            {"path": ("voice", "stt_beam_size"), "label": "STT Beam Size", "type": "int", "default": _default("voice", "stt_beam_size"), "min": 1, "max": 10, "step": 1},
            {"path": ("voice", "stt_best_of"), "label": "STT Best Of", "type": "int", "default": _default("voice", "stt_best_of"), "min": 1, "max": 8, "step": 1},
            {"path": ("voice", "stt_retry_on_low_confidence"), "label": "STT Retry On Low Confidence", "type": "bool", "default": _default("voice", "stt_retry_on_low_confidence")},
            {"path": ("voice", "stt_retry_beam_size"), "label": "STT Retry Beam Size", "type": "int", "default": _default("voice", "stt_retry_beam_size"), "min": 1, "max": 12, "step": 1},
            {"path": ("voice", "stt_no_speech_threshold"), "label": "STT No-Speech Threshold", "type": "float", "default": _default("voice", "stt_no_speech_threshold"), "min": 0.0, "max": 1.0, "step": 0.05},
            {"path": ("voice", "stt_apply_gain"), "label": "STT Auto Gain", "type": "bool", "default": _default("voice", "stt_apply_gain")},
            {"path": ("voice", "stt_target_rms"), "label": "STT Target RMS", "type": "float", "default": _default("voice", "stt_target_rms"), "min": 500.0, "max": 12000.0, "step": 100.0},
            {"path": ("voice", "stt_max_gain"), "label": "STT Max Gain", "type": "float", "default": _default("voice", "stt_max_gain"), "min": 1.0, "max": 8.0, "step": 0.1},
            {"path": ("voice", "stt_reject_non_latin_when_english"), "label": "Filter Non-English Noise", "type": "bool", "default": _default("voice", "stt_reject_non_latin_when_english")},
            {"path": ("voice", "stt_min_latin_ratio"), "label": "English Latin Ratio", "type": "float", "default": _default("voice", "stt_min_latin_ratio"), "min": 0.0, "max": 1.0, "step": 0.05},
            {"path": ("voice", "echo_guard_seconds"), "label": "Echo Guard Seconds", "type": "float", "default": _default("voice", "echo_guard_seconds"), "min": 0.0, "max": 8.0, "step": 0.1},
            {"path": ("voice", "echo_similarity_threshold"), "label": "Echo Similarity Threshold", "type": "float", "default": _default("voice", "echo_similarity_threshold"), "min": 0.5, "max": 1.0, "step": 0.01},
            {"path": ("voice", "echo_min_chars"), "label": "Echo Minimum Characters", "type": "int", "default": _default("voice", "echo_min_chars"), "min": 4, "max": 64, "step": 1},
            {"path": ("voice", "echo_retry_limit"), "label": "Echo Retry Limit", "type": "int", "default": _default("voice", "echo_retry_limit"), "min": 0, "max": 10, "step": 1},
            {"path": ("voice", "echo_retry_backoff_seconds"), "label": "Echo Retry Backoff", "type": "float", "default": _default("voice", "echo_retry_backoff_seconds"), "min": 0.0, "max": 3.0, "step": 0.05},
            {"path": ("voice", "silence_threshold_seconds"), "label": "Silence Threshold", "type": "float", "default": _default("voice", "silence_threshold_seconds"), "min": 0.5, "max": 5.0, "step": 0.1},
            {"path": ("voice", "min_speech_duration_seconds"), "label": "Minimum Speech Duration", "type": "float", "default": _default("voice", "min_speech_duration_seconds"), "min": 0.1, "max": 5.0, "step": 0.05},
            {"path": ("voice", "conversation_window_seconds"), "label": "Conversation Window", "type": "float", "default": _default("voice", "conversation_window_seconds"), "min": 1.0, "max": 30.0, "step": 0.5},
            {"path": ("voice", "vad_sensitivity"), "label": "VAD Sensitivity", "type": "float", "default": _default("voice", "vad_sensitivity"), "min": 0.0, "max": 1.0, "step": 0.05},
            {"path": ("voice", "wake_max_phrase_seconds"), "label": "Wake Max Phrase Seconds", "type": "float", "default": _default("voice", "wake_max_phrase_seconds"), "min": 1.0, "max": 8.0, "step": 0.1},
            {"path": ("voice", "wake_silence_seconds"), "label": "Wake Silence Seconds", "type": "float", "default": _default("voice", "wake_silence_seconds"), "min": 0.2, "max": 3.0, "step": 0.05},
            {"path": ("voice", "wake_min_speech_seconds"), "label": "Wake Min Speech Seconds", "type": "float", "default": _default("voice", "wake_min_speech_seconds"), "min": 0.1, "max": 2.0, "step": 0.05},
            {"path": ("voice", "wake_vad_sensitivity"), "label": "Wake VAD Sensitivity", "type": "float", "default": _default("voice", "wake_vad_sensitivity"), "min": 0.0, "max": 1.0, "step": 0.05},
            {"path": ("voice", "wake_language"), "label": "Wake Language", "type": "str", "default": _default("voice", "wake_language")},
            {"path": ("voice", "wake_initial_prompt"), "label": "Wake Initial Prompt", "type": "str", "default": _default("voice", "wake_initial_prompt")},
            {"path": ("voice", "mic_lock_timeout"), "label": "Mic Lock Timeout", "type": "float", "default": _default("voice", "mic_lock_timeout"), "min": 0.5, "max": 30.0, "step": 0.5},
            {"path": ("voice", "wake_listen_seconds"), "label": "Follow-up Listen Seconds", "type": "int", "default": _default("voice", "wake_listen_seconds"), "min": 1, "max": 20, "step": 1},
            {"path": ("voice", "follow_up_listen_seconds"), "label": "Conversation Follow-up Seconds", "type": "int", "default": _default("voice", "follow_up_listen_seconds"), "min": 1, "max": 20, "step": 1},
            {"path": ("voice", "wake_check_interval"), "label": "Wake Check Interval", "type": "float", "default": _default("voice", "wake_check_interval"), "min": 0.1, "max": 5.0, "step": 0.1},
            {"path": ("voice", "wake_cooldown_seconds"), "label": "Wake Cooldown Seconds", "type": "float", "default": _default("voice", "wake_cooldown_seconds"), "min": 0.5, "max": 30.0, "step": 0.5},
            {"path": ("voice", "interrupt_on_new_speech"), "label": "Interrupt On New Speech", "type": "bool", "default": _default("voice", "interrupt_on_new_speech")},
        ],
    },
    "Memory": {
        "description": "Conversation and persistence settings.",
        "fields": [
            {"path": ("memory", "db_path"), "label": "Memory File", "type": "path", "default": _default("memory", "db_path")},
            {"path": ("memory", "max_history"), "label": "Max History", "type": "int", "default": _default("memory", "max_history"), "min": 1, "max": 500, "step": 1},
            {"path": ("memory", "persist_history"), "label": "Persist History", "type": "bool", "default": _default("memory", "persist_history")},
            {"path": ("memory", "extract_facts"), "label": "Extract Facts", "type": "bool", "default": _default("memory", "extract_facts")},
        ],
    },
    "Event Bus": {
        "description": "Runtime event bus behavior.",
        "fields": [
            {"path": ("event_bus", "ordered"), "label": "Ordered Delivery", "type": "bool", "default": _default("event_bus", "ordered")},
            {"path": ("event_bus", "log_events"), "label": "Log Published Events", "type": "bool", "default": _default("event_bus", "log_events")},
        ],
    },
    "OS Bridge": {
        "description": "Desktop automation bridge.",
        "fields": [
            {"path": ("os_bridge", "enabled"), "label": "Enable OS Bridge", "type": "bool", "default": _default("os_bridge", "enabled")},
            {"path": ("os_bridge", "delay_between_actions"), "label": "Action Delay", "type": "float", "default": _default("os_bridge", "delay_between_actions"), "min": 0.0, "max": 10.0, "step": 0.05},
            {"path": ("os_bridge", "max_retries"), "label": "Max Retries", "type": "int", "default": _default("os_bridge", "max_retries"), "min": 0, "max": 10, "step": 1},
            {"path": ("os_bridge", "retry_delay"), "label": "Retry Delay", "type": "float", "default": _default("os_bridge", "retry_delay"), "min": 0.0, "max": 5.0, "step": 0.05},
            {"path": ("os_bridge", "continue_on_failure"), "label": "Continue On Failure", "type": "bool", "default": _default("os_bridge", "continue_on_failure")},
            {"path": ("os_bridge", "verify_after_actions"), "label": "Verify After Actions", "type": "bool", "default": _default("os_bridge", "verify_after_actions")},
            {"path": ("os_bridge", "verification_delay"), "label": "Verification Delay", "type": "float", "default": _default("os_bridge", "verification_delay"), "min": 0.0, "max": 10.0, "step": 0.05},
        ],
    },
    "Plugins": {
        "description": "Enable or disable plugins.",
        "plugin_names": PLUGIN_NAMES,
        "fields": [
            {"path": ("plugins", "enabled"), "label": "Enabled Plugins", "type": "plugin_list", "default": list(_default("plugins", "enabled"))},
        ],
    },
}
