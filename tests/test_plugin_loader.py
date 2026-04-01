import unittest
import tempfile
import os
import shutil
import time
from epet.core.event_bus import EventBus
from epet.core.hal import HALSimulator
from epet.core.memory import Memory
from epet.core.plugin_loader import PluginLoader

class TestPluginLoader(unittest.TestCase):
    def setUp(self):
        # Create a temporary directory for plugins
        self.plugins_dir = tempfile.mkdtemp()
        self.test_plugin_dir = os.path.join(self.plugins_dir, "test_plugin")
        os.makedirs(self.test_plugin_dir)
        with open(os.path.join(self.test_plugin_dir, "plugin.py"), "w") as f:
            f.write("""
def start(bus, hal, memory, config):
    bus.publish("test/loaded", {"status": "ok"})
""")
        self.bus = EventBus()
        self.hal = HALSimulator()
        self.memory = Memory(":memory:")

    def tearDown(self):
        shutil.rmtree(self.plugins_dir)

    def test_load_plugin(self):
        # Capture events
        events = []
        def catcher(topic, data):
            events.append((topic, data))
        self.bus.subscribe("test/loaded", catcher)

        loader = PluginLoader(["test_plugin"], self.bus, self.hal, self.memory, {},
                              plugins_dir=self.plugins_dir)
        loader.load_plugins()
        time.sleep(0.1)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0][0], "test/loaded")
        self.assertEqual(events[0][1], {"status": "ok"})

    def test_disabled_plugin_not_loaded(self):
        loader = PluginLoader([], self.bus, self.hal, self.memory, {},
                              plugins_dir=self.plugins_dir)
        events = []
        def catcher(topic, data):
            events.append((topic, data))
        self.bus.subscribe("test/loaded", catcher)
        loader.load_plugins()
        time.sleep(0.1)
        self.assertEqual(events, [])

if __name__ == "__main__":
    unittest.main()