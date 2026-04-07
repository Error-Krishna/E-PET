import logging
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
from simulator.face_renderer import SimpleRenderer as FaceRenderer
from simulator.input_sim import InputSimulator

def setup_logging(level):
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname).1s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
        force=True,
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)
    logging.getLogger("pygame").setLevel(logging.WARNING)
    logging.getLogger("numba").setLevel(logging.WARNING)
    warnings.filterwarnings("ignore", message="FP16 is not supported on CPU; using FP32 instead")
    warnings.filterwarnings("ignore", message="pkg_resources is deprecated as an API")

def main():
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
    logger.info("Config loaded")

    # Initialize systems
    bus = EventBus()
    logger.info("Event bus initialized")

    # HAL
    hal_mode = config.get("hardware", {}).get("mode", "simulator")
    hal_debug = config.get("hardware", {}).get("debug", False)
    if hal_mode != "simulator":
        logger.warning(f"Hardware mode '{hal_mode}' not supported; falling back to simulator")
    hal = HALSimulator(debug=hal_debug)
    logger.info("HAL initialized (simulator)")

    # Memory
    memory = Memory(get_database_path())
    logger.info("Memory system initialized")

    # Load plugins (including emotion, sound, idle)
    enabled_plugins = config.get("plugins", {}).get("enabled", [])
    loader = PluginLoader(enabled_plugins, bus, hal, memory, config)
    loader.load_plugins()
    logger.info("Plugin loading complete")

    # Start simulator components
    face_renderer = FaceRenderer(bus, hal, memory, config)
    face_renderer.start()
    input_sim = InputSimulator(bus)
    input_sim.start()

    # Play startup sound via event bus
    bus.publish("pet/sound/play", {"name": "startup"})

    # Main loop
    logger.info("E-Pet V1 running. Press Q to quit.")
    try:
        # Use event loop to process quit
        quit_event = threading.Event()
        def on_quit(topic, data):
            logger.info("Received quit event, shutting down...")
            quit_event.set()
        bus.subscribe("pet/system/quit", on_quit)

        # Keep main thread alive until quit
        quit_event.wait()
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received.")
    finally:
        # Clean shutdown
        logger.info("Shutting down...")
        face_renderer.stop()
        input_sim.stop()
        # Stop plugins that have stop methods (optional)
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
        time.sleep(0.5)  # Allow sound to play
        memory.close()
        logger.info("Goodbye!")

if __name__ == "__main__":
    main()
