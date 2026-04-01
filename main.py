import logging
import yaml
import time
import sys

from core.event_bus import EventBus
from core.hal import HALSimulator
from core.memory import Memory
from core.plugin_loader import PluginLoader

def setup_logging(level):
    """Configure logging based on config."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

def main():
    # Load config
    try:
        with open("config.yaml", "r") as f:
            config = yaml.safe_load(f)
    except Exception as e:
        print(f"Failed to load config.yaml: {e}")
        sys.exit(1)

    # Setup logging
    log_level = config.get("logging", {}).get("level", "INFO")
    setup_logging(log_level)
    logger = logging.getLogger(__name__)
    logger.info("Config loaded")

    # Initialize systems
    bus = EventBus()
    logger.info("Event bus initialized")

    # HAL (simulator)
    hal_mode = config.get("hardware", {}).get("mode", "simulator")
    hal_debug = config.get("hardware", {}).get("debug", False)
    if hal_mode != "simulator":
        logger.warning(f"Hardware mode '{hal_mode}' not supported; falling back to simulator")
    hal = HALSimulator(debug=hal_debug)
    logger.info("HAL initialized (simulator)")

    # Memory (SQLite)
    memory = Memory("epet.db")
    logger.info("Memory system initialized")

    # Load plugins
    enabled_plugins = config.get("plugins", {}).get("enabled", [])
    loader = PluginLoader(enabled_plugins, bus, hal, memory, config)
    loader.load_plugins()
    logger.info("Plugin loading complete")

    # End-to-end test: perform some memory operations
    memory.set("test_key", "test_value")
    val = memory.get("test_key")
    logger.info(f"Memory test: set/get test_key = {val}")

    memory.remember("user", "name", "E-Pet")
    name = memory.recall("user", "name")
    logger.info(f"Memory test: recalled user/name = {name}")

    memory.log_event("startup", "E-Pet V0 started")

    # Simple main loop to keep the program alive
    logger.info("E-Pet V0 running. Press Ctrl+C to exit.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        memory.close()
        sys.exit(0)

if __name__ == "__main__":
    main()