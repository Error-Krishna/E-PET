import argparse
import logging
import multiprocessing as mp
import warnings
import yaml
import time
import sys
import threading
from pathlib import Path

from core.config_validation import normalize_and_validate_config
from core.event_bus import EventBus
from core.hal import HALSimulator
from core.memory import Memory
from core.platform_utils import get_config_path, get_project_root
from core.plugin_loader import PluginLoader
from simulator.conversation_bridge import ConversationBridge
from simulator.face_renderer import SimpleRenderer as FaceRenderer
from simulator.conversation_window import run_conversation_window
from simulator.input_sim import InputSimulator
from epet_gui.ipc.bridge import (
    build_runtime_state,
    command_file_path,
    dispatch_command,
    read_json_file,
    remove_file_safely,
    state_file_path,
)

def setup_logging(level, log_file=None):
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | E-Pet | %(levelname).1s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
        force=True,
    )
    if log_file:
        try:
            log_path = Path(log_file).expanduser()
            if not log_path.is_absolute():
                log_path = get_config_path().parent / log_path
            log_path.parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(log_path, encoding="utf-8")
            file_handler.setFormatter(logging.Formatter("%(asctime)s | E-Pet | %(levelname).1s | %(name)s | %(message)s", datefmt="%H:%M:%S"))
            logging.getLogger().addHandler(file_handler)
        except Exception:
            pass
    for noisy in [
        "urllib3",
        "requests",
        "pygame",
        "numba",
        "httpx",
        "huggingface_hub",
        "faster_whisper",
    ]:
        logging.getLogger(noisy).setLevel(logging.WARNING)
    warnings.filterwarnings("ignore", message="FP16 is not supported on CPU; using FP32 instead")
    warnings.filterwarnings("ignore", message="pkg_resources is deprecated as an API")

def main():
    parser = argparse.ArgumentParser(description="Run the E-Pet backend")
    parser.add_argument("--headless", action="store_true", help="Disable the terminal renderer and keyboard simulator")
    args = parser.parse_args()

    # Load config
    try:
        config_path = get_config_path()
        with config_path.open("r", encoding="utf-8") as f:
            config = normalize_and_validate_config(yaml.safe_load(f))
    except Exception as e:
        print(f"Failed to load {get_config_path().name}: {e}")
        sys.exit(1)

    # Setup logging
    log_level = config.get("logging", {}).get("level", "INFO")
    setup_logging(log_level, config.get("logging", {}).get("file"))
    logger = logging.getLogger(__name__)
    logger.info("Boot | config loaded")

    logger.info("Boot | starting event bus")
    bus = EventBus(config)
    logger.info("Boot | event bus ready (ordered=%s)", config.get("event_bus", {}).get("ordered", False))

    hal_mode = config.get("hardware", {}).get("mode", "simulator")
    hal_debug = config.get("hardware", {}).get("debug", False)
    if hal_mode != "simulator":
        logger.info("Boot | hardware mode '%s' not supported; using simulator", hal_mode)
    logger.info("Boot | starting HAL simulator")
    hal = HALSimulator(debug=hal_debug)
    logger.info("Boot | HAL ready")

    logger.info("Boot | opening memory store")
    memory_db_raw = config.get("memory", {}).get("db_path") or "epet.db"
    memory_db_path = Path(memory_db_raw).expanduser()
    if not memory_db_path.is_absolute():
        memory_db_path = get_project_root() / memory_db_path
    memory = Memory(memory_db_path)
    logger.info("Boot | memory ready")

    enabled_plugins = config.get("plugins", {}).get("enabled", [])
    logger.info("Boot | loading plugins: %s", ", ".join(enabled_plugins) if enabled_plugins else "none")
    loader = PluginLoader(enabled_plugins, bus, hal, memory, config)
    loader.load_plugins()
    logger.info("Boot | plugins ready")

    runtime_stop = threading.Event()
    runtime_lock = threading.Lock()
    quit_requested = threading.Event()
    runtime_state = {
        "last_speech": "",
        "last_response": "",
        "voice_state": "idle",
        "last_event": "boot",
        "ai_backend": config.get("ai", {}).get("mode", "auto"),
    }

    def update_runtime_state(**kwargs):
        with runtime_lock:
            runtime_state.update(kwargs)

    def on_speech(topic, data):
        update_runtime_state(last_speech=str((data or {}).get("text", "")), last_event="speech")

    def on_response(topic, data):
        update_runtime_state(last_response=str((data or {}).get("text", "")), last_event="ai_response")

    def on_voice_state(topic, data):
        state = str((data or {}).get("state", "idle"))
        update_runtime_state(voice_state=state, last_event=f"voice_{state}")

    def on_emotion(topic, data):
        update_runtime_state(last_event=f"mood:{(data or {}).get('mood', 'neutral')}")

    def on_backend(topic, data):
        backend = str((data or {}).get("backend", config.get("ai", {}).get("mode", "auto")))
        update_runtime_state(ai_backend=backend, last_event=f"backend:{backend}")

    def on_quit(topic, data):
        logger.info("Runtime | shutdown requested")
        quit_requested.set()
        runtime_stop.set()
        if face_window is not None:
            try:
                face_window.stop()
            except Exception:
                pass
        if conversation_bridge is not None:
            try:
                conversation_bridge._enqueue("quit", {})
            except Exception:
                pass

    bus.subscribe("pet/input/speech", on_speech)
    bus.subscribe("pet/ai/response", on_response)
    bus.subscribe("pet/voice/tts_state", on_voice_state)
    bus.subscribe("pet/emotion/changed", on_emotion)
    bus.subscribe("pet/ai/backend", on_backend)
    bus.subscribe("pet/system/quit", on_quit)

    state_path = state_file_path()
    command_path = command_file_path()

    def ipc_loop():
        last_state_write = 0.0
        while not runtime_stop.is_set():
            now = time.time()
            if now - last_state_write >= 2.0:
                with runtime_lock:
                    snapshot = dict(runtime_state)
                plugin_names = ["emotion", "sound", "idle", "os_bridge", "voice", "ai"]
                try:
                    mood = memory.get("current_mood") or hal.get_state().get("face", "neutral")
                except Exception:
                    mood = hal.get_state().get("face", "neutral")
                state = build_runtime_state(
                    running=True,
                    mood=mood,
                    ai_mode=config.get("ai", {}).get("mode", "auto"),
                    voice_state=snapshot.get("voice_state", "idle"),
                    last_speech=snapshot.get("last_speech", ""),
                    last_response=snapshot.get("last_response", ""),
                    start_time=start_time,
                    plugins={name: name in enabled_plugins for name in plugin_names},
                    memory=memory,
                    last_event=snapshot.get("last_event", ""),
                    ai_backend=snapshot.get("ai_backend"),
                )
                try:
                    from epet_gui.ipc.bridge import write_json_atomic

                    write_json_atomic(state_path, state)
                except Exception as exc:
                    logger.debug("IPC state write failed: %s", exc)
                last_state_write = now
            command = read_json_file(command_path)
            if command is not None:
                remove_file_safely(command_path)
                try:
                    result = dispatch_command(bus, command, logger)
                    update_runtime_state(last_event=f"cmd:{command.get('command', '')}:{result}")
                    logger.info("GUI command processed: %s (%s)", command.get("command", ""), result)
                except Exception as exc:
                    logger.error("GUI command failed: %s", exc)
            # 100 ms polling keeps GUI command latency below a human-noticeable
            # threshold while only costing a handful of stat() checks per second.
            time.sleep(0.1)

    start_time = time.time()
    ipc_thread = threading.Thread(target=ipc_loop, daemon=True)
    ipc_thread.start()

    face_renderer = None
    face_window = None
    conversation_process = None
    conversation_bridge = None
    mp_ctx = mp.get_context("spawn")
    input_sim = None
    if args.headless:
        logger.info("Runtime | headless mode enabled")
        quit_requested.wait()
    else:
        try:
            from simulator.face_window import FaceWindow

            logger.info("Runtime | starting face window")
            face_window = FaceWindow(bus, hal, memory, config)
            logger.info("Runtime | starting temporary conversation window")
            conversation_queue = mp_ctx.Queue(maxsize=128)
            conversation_bridge = ConversationBridge(conversation_queue)
            conversation_bridge.bind(bus)
            conversation_process = mp_ctx.Process(
                target=run_conversation_window,
                args=(conversation_queue,),
                daemon=True,
            )
            conversation_process.start()
        except Exception as e:
            logger.warning("Runtime | face window unavailable, using terminal renderer: %s", e)
            logger.info("Runtime | starting terminal renderer")
            face_renderer = FaceRenderer(bus, hal, memory, config)
            face_renderer.start()

        logger.info("Runtime | starting keyboard input")
        input_sim = InputSimulator(bus)
        input_sim.start()

    bus.publish("pet/sound/play", {"name": "startup"})

    logger.info("Runtime | active (press q or close the window to quit)")
    try:
        if face_window is not None:
            face_window.run()
        else:
            quit_requested.wait()
    except KeyboardInterrupt:
        logger.info("Runtime | keyboard interrupt received")
    finally:
        logger.info("Runtime | stopping")
        runtime_stop.set()
        try:
            remove_file_safely(state_path)
        except Exception:
            pass
        if face_window is not None:
            face_window.stop()
        if conversation_bridge is not None:
            try:
                conversation_bridge._enqueue("quit", {})
            except Exception:
                pass
        if conversation_process is not None:
            conversation_process.join(timeout=1)
        if face_renderer is not None:
            face_renderer.stop()
        if input_sim is not None:
            input_sim.stop()
        for engine in [
            '_emotion_engine',
            '_sound_engine',
            '_idle_tick',
            '_os_bridge',
            '_wake',
            '_stt',
            '_tts',
            '_memory_manager',
            '_brain',
        ]:
            if hasattr(bus, engine):
                getattr(bus, engine).stop()
        bus.publish("pet/sound/play", {"name": "shutdown"})
        time.sleep(0.3)
        bus.shutdown()
        memory.close()
        logger.info("Runtime | goodbye")

if __name__ == "__main__":
    main()
