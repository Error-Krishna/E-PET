import argparse
import logging
import multiprocessing as mp
import warnings
import yaml
import time
import sys
import threading

from core.config_validation import normalize_and_validate_config
from core.event_bus import EventBus
from core.hal import HALSimulator
from core.memory import Memory
from core.platform_utils import get_config_path, get_database_path
from core.plugin_loader import PluginLoader
from simulator.conversation_bridge import ConversationBridge
from simulator.face_renderer import SimpleRenderer as FaceRenderer
from simulator.conversation_window import run_conversation_window
from simulator.input_sim import InputSimulator

def setup_logging(level):
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | E-Pet | %(levelname).1s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
        force=True,
    )
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
    setup_logging(log_level)
    logger = logging.getLogger(__name__)
    logger.info("Boot | config loaded")

    logger.info("Boot | starting event bus")
    bus = EventBus()
    logger.info("Boot | event bus ready")

    hal_mode = config.get("hardware", {}).get("mode", "simulator")
    hal_debug = config.get("hardware", {}).get("debug", False)
    if hal_mode != "simulator":
        logger.info("Boot | hardware mode '%s' not supported; using simulator", hal_mode)
    logger.info("Boot | starting HAL simulator")
    hal = HALSimulator(debug=hal_debug)
    logger.info("Boot | HAL ready")

    logger.info("Boot | opening memory store")
    memory = Memory(get_database_path())
    logger.info("Boot | memory ready")

    enabled_plugins = config.get("plugins", {}).get("enabled", [])
    logger.info("Boot | loading plugins: %s", ", ".join(enabled_plugins) if enabled_plugins else "none")
    loader = PluginLoader(enabled_plugins, bus, hal, memory, config)
    loader.load_plugins()
    logger.info("Boot | plugins ready")

    face_renderer = None
    face_window = None
    conversation_process = None
    conversation_bridge = None
    mp_ctx = mp.get_context("spawn")
    input_sim = None
    if args.headless:
        logger.info("Runtime | headless mode enabled")
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
            quit_event = threading.Event()

            def on_quit(topic, data):
                logger.info("Runtime | shutdown requested")
                quit_event.set()

            bus.subscribe("pet/system/quit", on_quit)

            quit_event.wait()
    except KeyboardInterrupt:
        logger.info("Runtime | keyboard interrupt received")
    finally:
        logger.info("Runtime | stopping")
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
