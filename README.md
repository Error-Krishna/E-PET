# E-Pet

E-Pet is a Python virtual pet with a shared backend, a native Qt control center, and an optional Streamlit dashboard. The same event bus powers mood updates, voice input, AI reasoning, memory, sound, and the simple face renderer.

## What's Included

- Backend runtime with plugins for emotion, idle behavior, sound, voice, AI, and OS automation
- Native E-Pet Control Center in `epet_gui/`
- Optional Streamlit dashboard in `streamlit_app.py`
- Simulator-based fallback for development on machines without the full hardware stack
- SQLite-backed memory and event logging
- Automated regression tests in `tests/test_overall_project.py`

## Install

Create a virtual environment and install the shared dependencies once for the whole project:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

On Windows, use `.venv\Scripts\activate` instead of `source .venv/bin/activate`.

## Run

Backend:

```bash
python main.py
```

Backend without the face window:

```bash
python main.py --headless
```

Native control center:

```bash
python epet_gui/main.py
```

Streamlit dashboard:

```bash
streamlit run streamlit_app.py
```

## Project Files

- `config.yaml` holds the runtime configuration
- `epet.db` stores persistent memory
- `.epet_state.json` and `.epet_cmd.json` are used by the GUI bridge
- `epet.log` captures runtime logs when logging is configured to write to file

## Configuration Notes

- `hardware.mode: simulator` is the safest default for a fresh clone
- `voice.wake_mode: whisper` avoids needing a Porcupine access key
- `ai.mode: auto` tries Groq first and falls back to Ollama if Groq is unavailable
- `ai.mode: online` always prefers Groq
- `ai.mode: offline` uses the local Ollama server

For voice models, point `voice.tts_model` at a real Piper `.onnx` file if you want Piper-backed TTS. If the binary is unavailable, E-Pet falls back to `pyttsx3` or terminal output.

## Controls

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

## Tests

```bash
python -m unittest tests.test_overall_project
```

## Notes

- The system is event-driven and multi-threaded.
- First launch can take longer while Whisper models download and cache.
- The temporary plain-text conversation window is meant for testing and can be ignored if you only use the face window or GUI.
