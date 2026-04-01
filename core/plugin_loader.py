import os
import importlib.util
import logging
from typing import List, Any

logger = logging.getLogger(__name__)

class PluginLoader:
    """Loads plugins from the plugins directory based on config."""
    def __init__(self, enabled_plugins: List[str], bus, hal, memory, config: dict, plugins_dir: str = None):
        self.enabled_plugins = enabled_plugins
        self.bus = bus
        self.hal = hal
        self.memory = memory
        self.config = config
        self.plugins_dir = plugins_dir or os.path.join(os.path.dirname(__file__), "..", "plugins")

    def load_plugins(self) -> None:
        """Scan the plugins directory and load enabled plugins."""
        if not os.path.isdir(self.plugins_dir):
            logger.warning(f"Plugins directory not found: {self.plugins_dir}")
            return

        for plugin_name in os.listdir(self.plugins_dir):
            plugin_path = os.path.join(self.plugins_dir, plugin_name)
            if not os.path.isdir(plugin_path):
                continue

            # Check if plugin is enabled
            if plugin_name not in self.enabled_plugins:
                logger.info(f"Plugin {plugin_name} is disabled, skipping")
                continue

            # Check for plugin.py
            plugin_file = os.path.join(plugin_path, "plugin.py")
            if not os.path.isfile(plugin_file):
                logger.error(f"Plugin {plugin_name} missing plugin.py")
                continue

            try:
                # Import the plugin module
                spec = importlib.util.spec_from_file_location(f"plugins.{plugin_name}", plugin_file)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)

                # Check for start function
                if not hasattr(module, "start"):
                    logger.error(f"Plugin {plugin_name} does not have start() function")
                    continue

                # Call start with bus, hal, memory, and config (could be plugin-specific config)
                plugin_config = self.config.get("plugins", {}).get(plugin_name, {})
                module.start(self.bus, self.hal, self.memory, plugin_config)
                logger.info(f"Plugin {plugin_name} loaded successfully")
            except Exception as e:
                logger.error(f"Failed to load plugin {plugin_name}: {e}", exc_info=True)