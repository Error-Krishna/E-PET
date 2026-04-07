import os
import importlib.util
import logging
import sys
import types
from pathlib import Path
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
        self.plugins_dir = str(Path(plugins_dir) if plugins_dir else Path(__file__).resolve().parent.parent / "plugins")

    def _ensure_plugins_package(self) -> None:
        """Expose the plugins directory as an importable package for relative imports."""
        package_name = "plugins"
        package = sys.modules.get(package_name)

        if package is None:
            package = types.ModuleType(package_name)
            package.__path__ = [self.plugins_dir]
            sys.modules[package_name] = package
            return

        package_paths = list(getattr(package, "__path__", []))
        if self.plugins_dir not in package_paths:
            package_paths.append(self.plugins_dir)
            package.__path__ = package_paths

    def load_plugins(self) -> None:
        """Scan the plugins directory and load enabled plugins."""
        if not os.path.isdir(self.plugins_dir):
            logger.warning(f"Plugins directory not found: {self.plugins_dir}")
            return

        self._ensure_plugins_package()

        for plugin_name in os.listdir(self.plugins_dir):
            plugin_path = os.path.join(self.plugins_dir, plugin_name)
            if not os.path.isdir(plugin_path):
                continue
            if plugin_name.startswith("__"):
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
                # Load plugin.py as a package module so relative imports like
                # "from .engine import start" resolve within the plugin folder.
                module_name = f"plugins.{plugin_name}"
                spec = importlib.util.spec_from_file_location(
                    module_name,
                    plugin_file,
                    submodule_search_locations=[plugin_path],
                )
                if spec is None or spec.loader is None:
                    raise ImportError(f"Could not create import spec for {module_name}")
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                spec.loader.exec_module(module)

                # Check for start function
                if not hasattr(module, "start"):
                    logger.error(f"Plugin {plugin_name} does not have start() function")
                    continue

                # Pass the full application config so plugins can read the
                # sections they depend on, like "idle" and "personality".
                module.start(self.bus, self.hal, self.memory, self.config)
                logger.info(f"Plugin {plugin_name} loaded successfully")
            except Exception as e:
                logger.error(f"Failed to load plugin {plugin_name}: {e}", exc_info=True)
