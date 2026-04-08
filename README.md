# E-Pet

E-Pet is a plugin-based virtual pet built in Python. It combines a mood engine, idle behavior, sound synthesis, a simple face window, voice input, and Groq-powered AI reasoning into one event-driven system.

## Current Features

- Keyboard touch simulation for pet interactions
- Emotion engine with persistent mood state
- Idle decay into bored/sleepy
- Procedural sound synthesis with pygame
- Minimal GUI face window for current mood
- Temporary plain-text conversation window for testing
- Wake trigger via Whisper phrase detection and keyboard fallback
- Speech-to-text via faster-whisper with microphone input
- Groq Cloud AI reasoning in online mode
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

### Windows

```powershell
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
```

### Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

## Run

```bash
python main.py
```

This opens the small face window automatically, keeps logs in your terminal, and also opens the temporary plain-text conversation window for testing.

Optional headless mode:

```bash
python main.py --headless
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

The event bus now supports an ordered mode controlled by `event_bus.ordered` in `config.yaml`. Ordered mode keeps FIFO processing per domain while preserving the existing async behavior when disabled.

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

E-Pet now uses a single Groq Cloud online path for model-backed responses.

Configure:
- `ai.mode: auto` to let E-Pet choose Groq when reachable and fall back offline when not
- `ai.mode: online` if you want it to try Groq every time and still fall back on failure
- `ai.mode: offline` if you want no network calls at all
- `GROQ_API_KEY` environment variable, or `ai.groq_api_key` as a local fallback
- `ai.groq_model: "llama-3.1-8b-instant"`

If you want the pet to run fully offline, set:
- `ai.mode: offline`

In offline mode, no network call is made and the AI response falls back immediately, while the rest of the pet still runs normally.
In auto mode, the app checks Groq reachability first and stays offline if it cannot connect.

The recommended low-latency model for this project is:
- `llama-3.1-8b-instant`

Groq uses an OpenAI-compatible chat-completions API, so the prompt-and-response flow stays simple and fast.
For secrets, prefer setting `GROQ_API_KEY` in your shell or launcher rather than storing the key in `config.yaml`.

## Config

Recommended config values for a fresh clone:

- `hardware.mode: simulator`
- `plugins.enabled`: keep `emotion`, `sound`, `idle`, `voice`, `ai`
- `personality.pet_name`: change the pet's name here
- `personality.name`: set your actual name
- `voice.wake_mode: whisper` if you do not have a Porcupine access key
- `voice.tts_model`: absolute path to your Piper voice model
- `ai.mode`: `auto` is the safest default for automatic online/offline switching
- `ai.mode`: `online` if you always want the app to try Groq first
- `ai.mode`: `offline` for no-network fallback
- `ai.groq_api_key`: leave blank if you are using `GROQ_API_KEY`
- `ai.groq_model`: keep the default unless you want to try another Groq model

The default `config.yaml` is designed to run without hardware, using the simulator and the voice fallbacks.

## Dependencies

Core dependencies in `requirements.txt`:
- `PyYAML`
- `numpy`
- `pygame`
- `requests`
- `faster-whisper`
- `groq`
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
- The temporary plain-text conversation window is only for testing and can be removed later without affecting the face window.
