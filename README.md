# E-Pet

E-Pet is a plugin-based virtual pet built in Python. It combines a mood engine, idle behavior, sound synthesis, terminal rendering, voice input, local/online AI reasoning, and local TTS into one event-driven system.

## Current Features

- Keyboard touch simulation for pet interactions
- Emotion engine with persistent mood state
- Idle decay into bored/sleepy
- Procedural sound synthesis with pygame
- Terminal renderer for current mood
- Wake trigger via keyboard fallback and optional Porcupine wake word
- Speech-to-text via Whisper with microphone input
- Local AI reasoning via Ollama-compatible API
- AI-driven emotion suggestions
- Text-to-speech via Piper with fallback terminal output
- Persistent facts and conversation history in SQLite
- Automated validation suites for V1 and V2 behavior

## Run

```bash
python main.py
```

The app now resolves `config.yaml` and `epet.db` from the project root, so it can
be launched from any working directory on Windows, macOS, or Linux.

## Main Controls

- `h` head touch
- `c` chin touch
- `b` back touch
- `e` belly touch
- `p` poke
- `s` shake
- `l` hold
- `d` double tap
- `m` cycle moods
- `t` play test sound
- `SPACE` wake trigger fallback
- `q` quit

## Architecture

Core:
- `core/event_bus.py`
- `core/plugin_loader.py`
- `core/hal.py`
- `core/memory.py`
- `core/config_validation.py`

Plugins:
- `plugins/emotion`
- `plugins/idle`
- `plugins/sound`
- `plugins/voice`
- `plugins/ai`

Simulator:
- `simulator/input_sim.py`
- `simulator/face_renderer.py`

## Voice / AI Setup

For local voice+AI mode, configure:
- `voice.piper_path`
- `voice.tts_model`
- `voice.whisper_model`
- `ai.mode = local`
- `ai.model = phi3`
- `ai.local_url`

Optional real wake word setup:
- install `pvporcupine`
- provide `voice.porcupine_access_key`
- set `voice.wake_keyword` to a built-in Porcupine keyword or provide `voice.porcupine_keyword_path`

Voice-related dependencies are optional. The core simulator, emotion engine,
and memory system will still run if `pvporcupine`, `openai-whisper`, `PyAudio`,
or `piper-tts` are missing.

For the most portable install, use:

```bash
pip install -r requirements.txt
```

Then add the voice packages only on platforms where you want microphone and TTS
support.

## Tests

Full validation:

```bash
python tests/final_everything_suite.py
```

## Notes

- The system is event-driven and multi-threaded.
- Keyboard fallback wake and speech flows are intended for development/testing.
- Local AI and TTS performance depends heavily on machine resources and model size.
