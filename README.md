# E-Pet

E-Pet is a plugin-based virtual pet built in Python. It combines a mood engine, idle behavior, sound synthesis, terminal rendering, voice input, local/online AI reasoning, and local TTS into one event-driven system.

## Current Features

- Keyboard touch simulation for pet interactions
- Emotion engine with persistent mood state
- Idle decay into bored/sleepy
- Procedural sound synthesis with pygame
- Terminal renderer for current mood
- Wake trigger via Whisper phrase detection and keyboard fallback
- Speech-to-text via faster-whisper with microphone input
- Local AI reasoning via Ollama-compatible API
- AI-driven emotion suggestions
- Text-to-speech via pyttsx3 with Piper support when available
- Persistent facts and conversation history in SQLite
- Automated validation suites for V1 and V2 behavior

## Quick Start

1. Create and activate a virtual environment.
2. Install dependencies with `pip install -r requirements.txt`.
3. Update `config.yaml` for your machine.
4. Run `python main.py`.

The app resolves `config.yaml` and `epet.db` from the project root, so it can be launched from any working directory on Windows, macOS, or Linux.

## Install

### macOS

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

If you want Porcupine wake words or Piper-backed TTS, you can also install:

```bash
python -m pip install pyaudio pvporcupine piper-tts
```

### Windows

```powershell
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
```

If you want Porcupine wake words or Piper-backed TTS, you can also install:

```powershell
python -m pip install pvporcupine piper-tts
```

If you choose to use `pyaudio` on Windows, install it only if your environment supports the wheel or build prerequisites.

### Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

If you want Porcupine wake words or Piper-backed TTS, you can also install:

```bash
python -m pip install pyaudio pvporcupine piper-tts
```

## Run

```bash
python main.py
```

On first run, `faster-whisper` may download the selected model. After that, startup is faster because the model is cached locally.

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

If the terminal is not interactive, keyboard fallback input is disabled and the app continues running in the background.

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

## Voice Setup

The project supports two wake modes:

- `wake_mode: "whisper"`: uses spoken wake phrase detection with `faster-whisper`
- `wake_mode: "auto"`: tries Porcupine first, then Whisper

For your current configuration, Whisper-only is the simplest path:

```yaml
voice:
  enabled: true
  wake_word: "hello"
  wake_mode: "whisper"
  piper_path: "piper"
  tts_model: "/absolute/path/to/en_US-lessac-medium.onnx"
  whisper_model: "tiny"
  wake_whisper_model: "tiny"
  record_seconds: 3
  wake_listen_seconds: 2
  wake_check_interval: 0.3
  wake_cooldown_seconds: 4.0
  interrupt_on_new_speech: true
```

Important voice notes:
- `voice.tts_model` must point to a real Piper `.onnx` file.
- The matching `.onnx.json` file should live next to it.
- `voice.whisper_model` controls the STT model.
- `voice.wake_whisper_model` controls the wake-word Whisper model.
- If you do not have a Porcupine access key, keep `wake_mode: "whisper"`.
- If `piper` is available as a binary, the app will use it; otherwise it falls back to `pyttsx3` or terminal output.

## AI Setup

For local AI mode, configure:
- `ai.mode: local`
- `ai.model: "phi3:latest"` or the Ollama model you actually have installed
- `ai.local_url`

The app sends prompts to an Ollama-compatible endpoint at `http://localhost:11434/api/generate`.

Make sure Ollama is running and the model exists locally:

```bash
ollama list
ollama pull phi3:latest
```

## Config

Recommended config values for a fresh clone:

- `hardware.mode: simulator`
- `plugins.enabled`: keep `emotion`, `sound`, `idle`, `voice`, `ai`
- `personality.name`: set your actual name
- `voice.wake_mode: whisper` if you do not have a Porcupine access key
- `voice.tts_model`: absolute path to your Piper voice model
- `ai.model`: a model you have pulled in Ollama

The default `config.yaml` is designed to run without hardware, using the simulator and the voice fallbacks.

## Dependencies

Core dependencies in `requirements.txt`:
- `PyYAML`
- `numpy`
- `pygame`
- `requests`
- `faster-whisper`
- `pvrecorder`
- `pyttsx3`

Voice support is included in the default install. The remaining enhancement packages are:
- `pvporcupine` for Porcupine wake words when you have an access key
- `piper-tts` if you want Piper-backed TTS binaries
- `pyaudio` only if you intentionally want to experiment with the legacy audio path

If you install only `requirements.txt`, you still get:
- wake-word detection with Whisper
- speech-to-text with faster-whisper
- TTS with `pyttsx3`

## Tests

Full validation:

```bash
python -m unittest tests.test_overall_project
```

## Notes

- The system is event-driven and multi-threaded.
- Keyboard fallback wake and speech flows are intended for development/testing.
- Local AI and TTS performance depends heavily on machine resources and model size.
- First launch may take longer while Whisper models are downloaded and cached.
