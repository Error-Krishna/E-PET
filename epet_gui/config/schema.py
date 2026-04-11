from __future__ import annotations

PLUGIN_NAMES = ["emotion", "sound", "idle", "os_bridge", "voice", "ai"]

CONFIG_SCHEMA = {
    "Dashboard": {
        "description": "At-a-glance status and quick actions.",
        "fields": [],
    },
    "General": {
        "description": "Core runtime and appearance settings.",
        "fields": [
            {"path": ("personality", "pet_name"), "label": "Pet Name", "type": "str", "default": "Mochi"},
            {"path": ("personality", "name"), "label": "Owner Name", "type": "str", "default": "krishna"},
            {"path": ("personality", "curiosity"), "label": "Curiosity", "type": "float", "default": 0.5, "min": 0.0, "max": 1.0, "step": 0.05},
            {"path": ("personality", "energy"), "label": "Energy", "type": "float", "default": 0.6, "min": 0.0, "max": 1.0, "step": 0.05},
            {"path": ("personality", "sociability"), "label": "Sociability", "type": "float", "default": 0.5, "min": 0.0, "max": 1.0, "step": 0.05},
            {"path": ("hardware", "mode"), "label": "Hardware Mode", "type": "enum", "choices": ["simulator", "normal", "headless"], "default": "simulator"},
            {"path": ("hardware", "debug"), "label": "Hardware Debug", "type": "bool", "default": False},
            {"path": ("logging", "level"), "label": "Log Level", "type": "enum", "choices": ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], "default": "INFO"},
            {"path": ("logging", "file"), "label": "Log File", "type": "path", "default": "epet.log"},
        ],
    },
    "AI": {
        "description": "Online Groq and offline Ollama controls.",
        "fields": [
            {"path": ("ai", "enabled"), "label": "Enable AI", "type": "bool", "default": True},
            {"path": ("ai", "mode"), "label": "AI Mode", "type": "enum", "choices": ["auto", "online", "offline"], "default": "auto"},
            {"path": ("ai", "groq_api_key"), "label": "Groq API Key", "type": "secret", "default": ""},
            {"path": ("ai", "groq_model"), "label": "Groq Model", "type": "str", "default": "llama-3.1-8b-instant"},
            {"path": ("ai", "ollama_host"), "label": "Ollama Host", "type": "str", "default": "http://localhost:11434"},
            {"path": ("ai", "ollama_model"), "label": "Ollama Model", "type": "str", "default": "phi3:mini"},
            {"path": ("ai", "ollama_keep_alive"), "label": "Ollama Keep Alive", "type": "str", "default": "10m"},
            {"path": ("ai", "ollama_temperature"), "label": "Ollama Temperature", "type": "float", "default": 0.7, "min": 0.0, "max": 2.0, "step": 0.05},
            {"path": ("ai", "ollama_num_ctx"), "label": "Ollama Context", "type": "int", "default": 1024, "min": 256, "max": 8192, "step": 128},
            {"path": ("ai", "ollama_num_predict"), "label": "Ollama Output Tokens", "type": "int", "default": 96, "min": 16, "max": 1024, "step": 16},
            {"path": ("ai", "request_timeout"), "label": "Request Timeout", "type": "int", "default": 60, "min": 5, "max": 300, "step": 5},
        ],
    },
    "Voice": {
        "description": "Wake word, STT, and TTS behavior.",
        "fields": [
            {"path": ("voice", "enabled"), "label": "Enable Voice", "type": "bool", "default": True},
            {"path": ("voice", "wake_word"), "label": "Wake Word", "type": "str", "default": "hello"},
            {"path": ("voice", "wake_mode"), "label": "Wake Mode", "type": "enum", "choices": ["auto", "whisper", "porcupine", "keyboard"], "default": "whisper"},
            {"path": ("voice", "stt_backend"), "label": "STT Backend", "type": "enum", "choices": ["auto", "whisper", "faster-whisper"], "default": "auto"},
            {"path": ("voice", "tts_backend"), "label": "TTS Backend", "type": "enum", "choices": ["piper", "pyttsx3", "none"], "default": "piper"},
            {"path": ("voice", "wake_keyword"), "label": "Wake Keyword", "type": "str", "default": "computer"},
            {"path": ("voice", "porcupine_access_key"), "label": "Porcupine Access Key", "type": "secret", "default": ""},
            {"path": ("voice", "porcupine_keyword_path"), "label": "Porcupine Keyword Path", "type": "path", "default": ""},
            {"path": ("voice", "piper_path"), "label": "Piper Path", "type": "str", "default": "piper"},
            {"path": ("voice", "tts_model"), "label": "TTS Model", "type": "path", "default": ""},
            {"path": ("voice", "whisper_model"), "label": "STT Model", "type": "str", "default": "tiny"},
            {"path": ("voice", "wake_whisper_model"), "label": "Wake Whisper Model", "type": "str", "default": "tiny"},
            {"path": ("voice", "record_seconds"), "label": "Record Seconds", "type": "int", "default": 3, "min": 1, "max": 30, "step": 1},
            {"path": ("voice", "mic_lock_timeout"), "label": "Mic Lock Timeout", "type": "float", "default": 5.0, "min": 0.5, "max": 30.0, "step": 0.5},
            {"path": ("voice", "wake_listen_seconds"), "label": "Follow-up Listen Seconds", "type": "int", "default": 2, "min": 1, "max": 20, "step": 1},
            {"path": ("voice", "follow_up_listen_seconds"), "label": "Conversation Follow-up Seconds", "type": "int", "default": 2, "min": 1, "max": 20, "step": 1},
            {"path": ("voice", "wake_check_interval"), "label": "Wake Check Interval", "type": "float", "default": 0.3, "min": 0.1, "max": 5.0, "step": 0.1},
            {"path": ("voice", "wake_cooldown_seconds"), "label": "Wake Cooldown Seconds", "type": "float", "default": 4.0, "min": 0.5, "max": 30.0, "step": 0.5},
            {"path": ("voice", "interrupt_on_new_speech"), "label": "Interrupt On New Speech", "type": "bool", "default": True},
        ],
    },
    "Memory": {
        "description": "Conversation and persistence settings.",
        "fields": [
            {"path": ("memory", "db_path"), "label": "Database Path", "type": "path", "default": "epet.db"},
            {"path": ("memory", "max_history"), "label": "Max History", "type": "int", "default": 20, "min": 1, "max": 500, "step": 1},
            {"path": ("memory", "persist_history"), "label": "Persist History", "type": "bool", "default": True},
            {"path": ("memory", "extract_facts"), "label": "Extract Facts", "type": "bool", "default": True},
        ],
    },
    "Event Bus": {
        "description": "Runtime event bus behavior.",
        "fields": [
            {"path": ("event_bus", "ordered"), "label": "Ordered Delivery", "type": "bool", "default": True},
            {"path": ("event_bus", "log_events"), "label": "Log Published Events", "type": "bool", "default": False},
        ],
    },
    "OS Bridge": {
        "description": "Desktop automation bridge.",
        "fields": [
            {"path": ("os_bridge", "enabled"), "label": "Enable OS Bridge", "type": "bool", "default": True},
            {"path": ("os_bridge", "delay_between_actions"), "label": "Action Delay", "type": "float", "default": 0.3, "min": 0.0, "max": 10.0, "step": 0.05},
            {"path": ("os_bridge", "max_retries"), "label": "Max Retries", "type": "int", "default": 2, "min": 0, "max": 10, "step": 1},
            {"path": ("os_bridge", "retry_delay"), "label": "Retry Delay", "type": "float", "default": 0.15, "min": 0.0, "max": 5.0, "step": 0.05},
            {"path": ("os_bridge", "continue_on_failure"), "label": "Continue On Failure", "type": "bool", "default": False},
        ],
    },
    "Plugins": {
        "description": "Enable or disable plugins.",
        "plugin_names": PLUGIN_NAMES,
        "fields": [
            {"path": ("plugins", "enabled"), "label": "Enabled Plugins", "type": "plugin_list", "default": PLUGIN_NAMES},
        ],
    },
}
