import json
import os
import shutil
import sys
import tempfile
import time
import threading
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.config_validation import DEFAULT_CONFIG, normalize_and_validate_config
from core.event_bus import EventBus
from core.hal import HALSimulator
from core.memory import Memory
from core.platform_utils import get_config_path, get_database_path, resolve_executable
from core.plugin_loader import PluginLoader
from core.utils import profile
from plugins.ai import brain as brain_module
from plugins.ai import plugin as ai_plugin
from plugins.ai.brain import AIBrain
from plugins.ai.memory_manager import MemoryManager
from plugins.emotion.engine import EmotionEngine, MOODS, TOUCH_MOOD
from plugins.idle.engine import IdleTick
from plugins.os_bridge import executor as os_bridge_executor_module
from plugins.os_bridge import plugin as os_bridge_plugin
from plugins.os_bridge.actions import apps as os_bridge_apps_module
from plugins.os_bridge.actions import keyboard as os_bridge_keyboard_module
from plugins.os_bridge.executor import OSBridgeExecutor
from plugins.sound.engine import SoundEngine
from plugins.voice import plugin as voice_plugin
from plugins.voice import stt as stt_module
from plugins.voice import tts as tts_module
from plugins.voice import wake as wake_module
from plugins.voice.stt import SpeechToText
from plugins.voice.tts import TextToSpeech
from plugins.voice.wake import WakeWordDetector
from simulator.face_renderer import SimpleRenderer
from simulator.input_sim import InputSimulator, KEY_MAPPINGS


class BaseOverallTest(unittest.TestCase):
    def setUp(self):
        self.memory_file = tempfile.NamedTemporaryFile(delete=False)
        self.memory_file.close()
        self.bus = EventBus()
        self.hal = HALSimulator(debug=False)
        self.memory = Memory(self.memory_file.name)
        self.config = normalize_and_validate_config(
            {
                "plugins": {"enabled": ["emotion", "sound", "idle", "voice", "ai"]},
                "personality": {
                    "pet_name": "Mochi",
                    "curiosity": 0.5,
                    "energy": 0.6,
                    "sociability": 0.5,
                    "name": "krishna",
                },
                "idle": {"bored_after": 0.15, "sleepy_after": 0.30},
                "voice": {
                    "enabled": True,
                    "wake_word": "hello",
                    "wake_mode": "whisper",
                    "piper_path": "piper",
                    "tts_model": "/Users/krishnagoyal/Desktop/epet/voices/en_US-lessac-medium.onnx",
                    "whisper_model": "tiny",
                    "wake_whisper_model": "tiny",
                    "record_seconds": 1,
                    "wake_listen_seconds": 1,
                    "wake_check_interval": 0.1,
                    "wake_cooldown_seconds": 0.5,
                    "interrupt_on_new_speech": True,
                },
                "ai": {
                    "enabled": True,
                    "mode": "online",
                    "groq_api_key": "test-groq-key",
                    "groq_model": "llama-3.1-8b-instant",
                    "ollama_host": "http://localhost:11434",
                    "ollama_model": "phi3:mini",
                    "ollama_keep_alive": "10m",
                    "ollama_temperature": 0.7,
                    "ollama_num_ctx": 1024,
                    "ollama_num_predict": 96,
                    "request_timeout": 60,
                },
                "memory": {"max_history": 20, "persist_history": True, "extract_facts": True},
            }
        )

    def tearDown(self):
        try:
            self.bus.shutdown()
        finally:
            self.memory.close()
            if os.path.exists(self.memory_file.name):
                os.unlink(self.memory_file.name)

    @staticmethod
    def wait(delay=0.12):
        time.sleep(delay)


class TestConfigAndPlatform(BaseOverallTest):
    def test_default_config_values_exist(self):
        self.assertEqual(DEFAULT_CONFIG["personality"]["name"], "krishna")
        self.assertEqual(DEFAULT_CONFIG["personality"]["pet_name"], "Mochi")
        self.assertEqual(DEFAULT_CONFIG["ai"]["mode"], "auto")
        self.assertEqual(DEFAULT_CONFIG["ai"]["groq_model"], "llama-3.1-8b-instant")
        self.assertEqual(DEFAULT_CONFIG["ai"]["ollama_host"], "http://localhost:11434")
        self.assertEqual(DEFAULT_CONFIG["ai"]["ollama_model"], "phi3:mini")
        self.assertEqual(DEFAULT_CONFIG["ai"]["ollama_keep_alive"], "10m")
        self.assertEqual(DEFAULT_CONFIG["ai"]["ollama_temperature"], 0.7)
        self.assertEqual(DEFAULT_CONFIG["ai"]["ollama_num_ctx"], 1024)
        self.assertEqual(DEFAULT_CONFIG["ai"]["ollama_num_predict"], 96)
        self.assertTrue(DEFAULT_CONFIG["event_bus"]["ordered"])
        self.assertEqual(DEFAULT_CONFIG["voice"]["wake_mode"], "auto")
        self.assertEqual(DEFAULT_CONFIG["os_bridge"]["max_retries"], 2)
        self.assertFalse(DEFAULT_CONFIG["os_bridge"]["continue_on_failure"])

    def test_config_validation_normalizes_and_clamps(self):
        config = normalize_and_validate_config(
            {
                "plugins": {"enabled": ["emotion", "emotion", "sound"]},
                "logging": {"level": "verbose"},
                "idle": {"bored_after": 50, "sleepy_after": 10},
                "voice": {
                    "record_seconds": 0,
                    "wake_listen_seconds": 0,
                    "wake_check_interval": 0.01,
                    "wake_cooldown_seconds": 0.1,
                },
                "ai": {"request_timeout": 1},
            }
        )
        self.assertEqual(config["plugins"]["enabled"], ["emotion", "sound"])
        self.assertEqual(config["logging"]["level"], "INFO")
        self.assertEqual(config["idle"]["sleepy_after"], 50)
        self.assertEqual(config["voice"]["record_seconds"], 1)
        self.assertEqual(config["voice"]["wake_listen_seconds"], 1)
        self.assertGreaterEqual(config["voice"]["wake_check_interval"], 0.1)
        self.assertGreaterEqual(config["voice"]["wake_cooldown_seconds"], 0.5)
        self.assertGreaterEqual(config["ai"]["request_timeout"], 5)

    def test_project_paths_and_executable_resolution(self):
        self.assertTrue(get_config_path().is_absolute())
        self.assertEqual(get_config_path().name, "config.yaml")
        self.assertTrue(get_database_path().is_absolute())
        self.assertEqual(get_database_path().name, "epet.db")
        self.assertIsNotNone(resolve_executable(sys.executable))

    def test_profile_decorator_preserves_return_value(self):
        calls = []

        @profile
        def _sample(value):
            calls.append(value)
            return value + 1

        with mock.patch.dict(os.environ, {"EPET_PROFILE": "1"}, clear=False), \
             mock.patch("builtins.print") as mocked_print:
            self.assertEqual(_sample(2), 3)
            mocked_print.assert_called_once()
        self.assertEqual(calls, [2])

    def test_memory_creates_parent_directories(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            nested = Path(tmpdir) / "nested" / "sub" / "epet.db"
            memory = Memory(str(nested))
            try:
                memory.set("hello", "world")
                self.assertEqual(memory.get("hello"), "world")
                self.assertTrue(nested.exists())
            finally:
                memory.close()


class TestEventBusAndMemory(BaseOverallTest):
    def test_exact_subscription_delivery(self):
        received = []
        self.bus.subscribe("pet/test/exact", lambda topic, data: received.append((topic, data)))
        self.bus.publish("pet/test/exact", {"ok": True})
        self.wait()
        self.assertEqual(received, [("pet/test/exact", {"ok": True})])

    def test_wildcard_subscription_delivery(self):
        received = []
        self.bus.subscribe("pet/*", lambda topic, data: received.append((topic, data)))
        self.bus.subscribe("pet/input/*", lambda topic, data: received.append((topic, data)))
        self.bus.publish("pet/input/touch", {"zone": "head"})
        self.bus.publish("pet/status", {"mood": "happy"})
        self.wait()
        topics = [topic for topic, _ in received]
        self.assertEqual(topics.count("pet/input/touch"), 2)
        self.assertEqual(topics.count("pet/status"), 1)

    def test_multiple_subscribers(self):
        calls = []
        self.bus.subscribe("test", lambda topic, data: calls.append("cb1"))
        self.bus.subscribe("test", lambda topic, data: calls.append("cb2"))
        self.bus.publish("test", None)
        self.wait()
        self.assertEqual(len(calls), 2)
        self.assertIn("cb1", calls)
        self.assertIn("cb2", calls)

    def test_no_matching_subscriber_is_safe(self):
        self.bus.publish("nonexistent", "data")
        self.wait()

    def test_topic_routing_uses_slow_pool_for_voice_and_ai(self):
        self.bus.subscribe("pet/voice/tts_state", lambda topic, data: None)
        with mock.patch.object(self.bus._slow_pool, "submit", wraps=self.bus._slow_pool.submit) as slow_submit, \
             mock.patch.object(self.bus._fast_pool, "submit", wraps=self.bus._fast_pool.submit) as fast_submit:
            self.bus.publish("pet/voice/tts_state", {"state": "idle"})
            self.wait()
        self.assertTrue(slow_submit.called)
        self.assertFalse(fast_submit.called)

    def test_topic_routing_uses_fast_pool_for_other_events(self):
        self.bus.subscribe("pet/input/touch", lambda topic, data: None)
        with mock.patch.object(self.bus._slow_pool, "submit", wraps=self.bus._slow_pool.submit) as slow_submit, \
             mock.patch.object(self.bus._fast_pool, "submit", wraps=self.bus._fast_pool.submit) as fast_submit:
            self.bus.publish("pet/input/touch", {"zone": "head"})
            self.wait()
        self.assertTrue(fast_submit.called)
        self.assertFalse(slow_submit.called)

    def test_shutdown_does_not_raise(self):
        self.bus.shutdown()

    def test_ordered_bus_preserves_publish_order_and_adds_metadata(self):
        ordered_bus = EventBus({"event_bus": {"ordered": True}})
        received = []
        ordered_bus.subscribe("pet/emotion/*", lambda topic, data: received.append(data))
        try:
            ordered_bus.publish("pet/emotion/one", {"n": 1})
            ordered_bus.publish("pet/emotion/two", {"n": 2})
            ordered_bus.publish("pet/emotion/three", {"n": 3})
            self.wait(0.5)
        finally:
            ordered_bus.shutdown()
        self.assertEqual([item["data"]["n"] for item in received], [1, 2, 3])
        self.assertEqual([item["seq"] for item in received], sorted(item["seq"] for item in received))
        self.assertTrue(all("timestamp" in item for item in received))
        self.assertTrue(all(item["topic"].startswith("pet/emotion/") for item in received))

    def test_ordered_bus_allows_parallel_cross_domain_processing(self):
        ordered_bus = EventBus({"event_bus": {"ordered": True}})
        start_times = {}
        done = threading.Event()

        def make_handler(name):
            def _handler(topic, data):
                start_times[name] = time.time()
                time.sleep(0.2)
                if len(start_times) == 2:
                    done.set()
            return _handler

        ordered_bus.subscribe("pet/voice/*", make_handler("voice"))
        ordered_bus.subscribe("pet/ai/*", make_handler("ai"))
        try:
            start = time.time()
            ordered_bus.publish("pet/voice/test", {"text": "one"})
            ordered_bus.publish("pet/ai/test", {"text": "two"})
            self.assertTrue(done.wait(1.0))
            elapsed = time.time() - start
        finally:
            ordered_bus.shutdown()
        self.assertLess(elapsed, 0.35)
        self.assertIn("voice", start_times)
        self.assertIn("ai", start_times)

    def test_ordered_bus_handler_can_offload_slow_work(self):
        ordered_bus = EventBus({"event_bus": {"ordered": True}})
        completed = []
        worker_done = threading.Event()

        def handler(topic, data):
            def slow_task():
                time.sleep(0.2)
                completed.append(data["data"]["step"])
                worker_done.set()

            threading.Thread(target=slow_task, daemon=True).start()

        ordered_bus.subscribe("pet/system/*", handler)
        try:
            start = time.time()
            ordered_bus.publish("pet/system/test", {"step": 1})
            publish_elapsed = time.time() - start
            self.assertLess(publish_elapsed, 0.05)
            self.assertTrue(worker_done.wait(1.0))
        finally:
            ordered_bus.shutdown()
        self.assertEqual(completed, [1])

    def test_key_value_and_categorized_memory_round_trip(self):
        self.memory.set("current_mood", "happy")
        self.memory.remember("bond", "favorite_zone", "head")
        self.assertEqual(self.memory.get("current_mood"), "happy")
        self.assertEqual(self.memory.recall("bond", "favorite_zone"), "head")

        self.memory.set("current_mood", "sleepy")
        self.memory.remember("bond", "favorite_zone", "chin")
        self.assertEqual(self.memory.get("current_mood"), "sleepy")
        self.assertEqual(self.memory.recall("bond", "favorite_zone"), "chin")

    def test_event_log_is_persisted(self):
        self.memory.log_event("touch", '{"zone": "head"}')
        rows = self.memory.conn.execute("SELECT event_type, data FROM events").fetchall()
        self.assertEqual(rows, [("touch", '{"zone": "head"}')])

    def test_sqlite_pragmas_are_enabled(self):
        journal_mode = self.memory.conn.execute("PRAGMA journal_mode").fetchone()[0]
        synchronous = self.memory.conn.execute("PRAGMA synchronous").fetchone()[0]
        self.assertEqual(str(journal_mode).lower(), "wal")
        self.assertEqual(int(synchronous), 1)

    def test_data_persists_across_reopen(self):
        self.memory.set("current_mood", "love")
        self.memory.remember("bond", "streak", "3")
        self.memory.close()

        reopened = Memory(self.memory_file.name)
        try:
            self.assertEqual(reopened.get("current_mood"), "love")
            self.assertEqual(reopened.recall("bond", "streak"), "3")
        finally:
            reopened.close()
            self.memory = Memory(self.memory_file.name)


class TestPluginLoaderAndWiring(BaseOverallTest):
    def setUp(self):
        super().setUp()
        self.plugins_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.plugins_dir)
        super().tearDown()

    def _write_plugin(self, name, plugin_source, engine_source=None):
        plugin_path = Path(self.plugins_dir) / name
        plugin_path.mkdir()
        (plugin_path / "plugin.py").write_text(plugin_source, encoding="utf-8")
        if engine_source is not None:
            (plugin_path / "engine.py").write_text(engine_source, encoding="utf-8")

    def test_loader_imports_relative_plugin_engine_and_passes_full_config(self):
        self._write_plugin(
            "relative_ok",
            "from .engine import start\n",
            (
                "def start(bus, hal, memory, config):\n"
                "    bus.publish('plugin/loaded', {\n"
                "        'energy': config['personality']['energy'],\n"
                "        'bored_after': config['idle']['bored_after'],\n"
                "    })\n"
            ),
        )

        received = []
        self.bus.subscribe("plugin/loaded", lambda topic, data: received.append(data))
        loader = PluginLoader(
            ["relative_ok"],
            self.bus,
            self.hal,
            self.memory,
            self.config,
            plugins_dir=self.plugins_dir,
        )
        loader.load_plugins()
        self.wait()
        self.assertEqual(received, [{"energy": 0.6, "bored_after": 0.15}])

    def test_loader_skips_disabled_plugins(self):
        self._write_plugin(
            "disabled_plugin",
            "def start(bus, hal, memory, config):\n    bus.publish('plugin/disabled_should_not_run', {})\n",
        )
        received = []
        self.bus.subscribe("plugin/disabled_should_not_run", lambda topic, data: received.append(data))
        loader = PluginLoader([], self.bus, self.hal, self.memory, self.config, plugins_dir=self.plugins_dir)
        loader.load_plugins()
        self.wait()
        self.assertEqual(received, [])

    def test_ai_plugin_start_sets_bus_references(self):
        fake_manager = mock.Mock()
        fake_brain = mock.Mock()

        with mock.patch.object(ai_plugin, "MemoryManager", return_value=fake_manager), \
             mock.patch.object(ai_plugin, "AIBrain", return_value=fake_brain):
            ai_plugin.start(self.bus, self.hal, self.memory, self.config)

        fake_manager.start.assert_called_once()
        fake_brain.start.assert_called_once()
        self.assertIs(self.bus._memory_manager, fake_manager)
        self.assertIs(self.bus._brain, fake_brain)

    def test_ai_plugin_respects_disabled_flag(self):
        cfg = dict(self.config)
        cfg["ai"] = {"enabled": False}
        with mock.patch.object(ai_plugin, "MemoryManager") as manager_cls, \
             mock.patch.object(ai_plugin, "AIBrain") as brain_cls:
            ai_plugin.start(self.bus, self.hal, self.memory, cfg)
        manager_cls.assert_not_called()
        brain_cls.assert_not_called()

    def test_voice_plugin_start_sets_bus_references(self):
        fake_wake = mock.Mock()
        fake_stt = mock.Mock()
        fake_tts = mock.Mock()

        with mock.patch.object(voice_plugin, "WakeWordDetector", return_value=fake_wake), \
             mock.patch.object(voice_plugin, "SpeechToText", return_value=fake_stt), \
             mock.patch.object(voice_plugin, "TextToSpeech", return_value=fake_tts):
            voice_plugin.start(self.bus, self.hal, self.memory, self.config)

        fake_wake.start.assert_called_once()
        fake_stt.start.assert_called_once()
        fake_tts.start.assert_called_once()
        self.assertIs(self.bus._wake, fake_wake)
        self.assertIs(self.bus._stt, fake_stt)
        self.assertIs(self.bus._tts, fake_tts)

    def test_os_bridge_plugin_respects_disabled_flag(self):
        cfg = json.loads(json.dumps(self.config))
        cfg["os_bridge"]["enabled"] = False
        with mock.patch.object(os_bridge_plugin, "OSBridgeExecutor") as executor_cls:
            os_bridge_plugin.start(self.bus, self.hal, self.memory, cfg)
        executor_cls.assert_not_called()
        self.assertFalse(hasattr(self.bus, "_os_bridge"))


class TestOSBridge(BaseOverallTest):
    def test_os_bridge_executor_processes_steps_in_order_and_publishes_status(self):
        executor = OSBridgeExecutor(self.bus, self.hal, self.memory, self.config)
        executor.delay_between_actions = 0
        statuses = []
        calls = []
        self.bus.subscribe("pet/task/status", lambda topic, data: statuses.append(data))
        with mock.patch.object(os_bridge_executor_module, "open_app", side_effect=lambda name: calls.append(("open_app", name))), \
             mock.patch.object(os_bridge_executor_module, "type_text", side_effect=lambda text: calls.append(("type_text", text))):
            executor._execute_task(
                {
                    "task_id": "task_123",
                    "actions": [
                        {"step": 2, "type": "type_text", "text": "hello world"},
                        {"step": 1, "type": "open_app", "target": "notepad"},
                    ],
                }
            )
        self.wait(0.2)
        self.assertEqual(calls, [("open_app", "notepad"), ("type_text", "hello world")])
        completed_statuses = [item for item in statuses if item["status"] == "completed"]
        self.assertEqual([(item["step"], item["status"]) for item in completed_statuses], [(1, "completed"), (2, "completed")])
        state = executor.get_task_state("task_123")
        self.assertEqual(state["status"], "completed")
        self.assertEqual(state["current_step"], 2)

    def test_os_bridge_executor_retries_open_app_fallbacks_before_failing(self):
        executor = OSBridgeExecutor(self.bus, self.hal, self.memory, self.config)
        executor.delay_between_actions = 0
        executor.retry_delay = 0
        executor.max_retries = 2
        attempts = []
        statuses = []
        self.bus.subscribe("pet/task/status", lambda topic, data: statuses.append(data))

        def fake_open_app(name):
            attempts.append(name)
            if name != "__default_browser__":
                raise FileNotFoundError(f"app not found: {name}")

        with mock.patch.object(os_bridge_executor_module, "open_app", side_effect=fake_open_app):
            executor._execute_task(
                {
                    "task_id": "task_retry",
                    "actions": [
                        {"step": 1, "type": "open_app", "target": "chrome"},
                    ],
                }
            )

        self.assertEqual(attempts[:3], ["chrome", "google chrome", "__default_browser__"])
        self.wait(0.2)
        state = executor.get_task_state("task_retry")
        self.assertEqual(state["status"], "completed")
        self.assertTrue(any(item["status"] == "running" and item["step"] == 1 for item in statuses))

    def test_os_bridge_executor_handles_invalid_app_name_failure(self):
        executor = OSBridgeExecutor(self.bus, self.hal, self.memory, self.config)
        statuses = []
        self.bus.subscribe("pet/task/status", lambda topic, data: statuses.append(data))
        with mock.patch.object(
            os_bridge_executor_module,
            "open_app",
            side_effect=FileNotFoundError("app not found: missing-app"),
        ):
            executor._execute_task(
                {
                    "task_id": "task_fail",
                    "actions": [
                        {"step": 1, "type": "open_app", "target": "missing-app"},
                    ],
                }
            )
        self.wait(0.2)
        self.assertEqual(statuses[-1]["status"], "failed")
        self.assertIn("app not found", statuses[-1]["error"])

    def test_os_bridge_executor_can_continue_after_failure_when_configured(self):
        cfg = json.loads(json.dumps(self.config))
        cfg["os_bridge"]["continue_on_failure"] = True
        executor = OSBridgeExecutor(self.bus, self.hal, self.memory, cfg)
        executor.delay_between_actions = 0
        statuses = []
        calls = []
        self.bus.subscribe("pet/task/status", lambda topic, data: statuses.append(data))
        with mock.patch.object(os_bridge_executor_module, "open_app", side_effect=FileNotFoundError("app not found: missing")), \
             mock.patch.object(os_bridge_executor_module, "type_text", side_effect=lambda text: calls.append(("type_text", text))):
            executor._execute_task(
                {
                    "task_id": "task_continue",
                    "actions": [
                        {"step": 1, "type": "open_app", "target": "missing"},
                        {"step": 2, "type": "type_text", "text": "still runs"},
                    ],
                }
            )
        self.wait(0.2)
        self.assertEqual(calls, [("type_text", "still runs")])
        self.assertEqual(executor.get_task_state("task_continue")["status"], "failed")
        self.assertTrue(any(item["step"] == 2 and item["status"] == "completed" for item in statuses))

    def test_os_bridge_executor_normalizes_wrapped_event_payload(self):
        executor = OSBridgeExecutor(self.bus, self.hal, self.memory, self.config)
        wrapped = {
            "seq": 9,
            "timestamp": time.time(),
            "topic": "pet/ai/action",
            "source": "ai",
            "data": {
                "task_id": "task_wrapped",
                "actions": [{"step": 1, "type": "press", "key": "enter"}],
            },
        }
        normalized = executor._normalize_payload(wrapped)
        self.assertIsNotNone(normalized)
        self.assertEqual(normalized["task_id"], "task_wrapped")
        self.assertEqual(normalized["actions"][0]["type"], "press")

    def test_os_bridge_keyboard_helpers_proxy_pyautogui(self):
        fake_pag = mock.Mock()
        original = os_bridge_keyboard_module._pyautogui
        try:
            os_bridge_keyboard_module._pyautogui = fake_pag
            os_bridge_keyboard_module.type_text("hello")
            os_bridge_keyboard_module.press("enter")
            os_bridge_keyboard_module.hotkey("ctrl", "s")
        finally:
            os_bridge_keyboard_module._pyautogui = original
        fake_pag.write.assert_called_once()
        fake_pag.press.assert_called_once_with("enter")
        fake_pag.hotkey.assert_called_once_with("ctrl", "s", interval=0.03)

    def test_os_bridge_open_app_uses_platform_specific_command(self):
        with mock.patch.object(os_bridge_apps_module.platform, "system", return_value="darwin"), \
             mock.patch.object(os_bridge_apps_module.subprocess, "Popen") as popen:
            process = mock.Mock()
            process.returncode = 0
            process.communicate.return_value = (b"", b"")
            popen.return_value = process
            os_bridge_apps_module.open_app("TextEdit")
        popen.assert_called_once()
        self.assertEqual(popen.call_args.args[0], ["open", "-a", "TextEdit"])

    def test_os_bridge_open_app_windows_and_linux_paths(self):
        with mock.patch.object(os_bridge_apps_module.platform, "system", return_value="windows"), \
             mock.patch.object(os_bridge_apps_module.subprocess, "Popen") as popen:
            win_process = mock.Mock()
            win_process.wait.return_value = 0
            popen.return_value = win_process
            os_bridge_apps_module.open_app("notepad")
        self.assertEqual(popen.call_args.args[0], ["start", "notepad"])
        self.assertTrue(popen.call_args.kwargs["shell"])

        with mock.patch.object(os_bridge_apps_module.platform, "system", return_value="linux"), \
             mock.patch.object(os_bridge_apps_module.shutil, "which", return_value="/usr/bin/gedit"), \
             mock.patch.object(os_bridge_apps_module.subprocess, "Popen") as popen_linux:
            os_bridge_apps_module.open_app("gedit")
        self.assertEqual(popen_linux.call_args.args[0], ["gedit"])


class TestEmotionIdleSoundAndRenderer(BaseOverallTest):
    def setUp(self):
        super().setUp()
        self.emotion = EmotionEngine(self.bus, self.hal, self.memory, self.config)
        self.emotion.start()
        self.emotion_events = []
        self.bus.subscribe("pet/emotion/changed", lambda topic, data: self.emotion_events.append(data))

    def tearDown(self):
        self.emotion.stop()
        super().tearDown()

    def test_all_moods_have_complete_metadata(self):
        for mood_name, mood_data in MOODS.items():
            self.assertIn("face", mood_data, mood_name)
            self.assertIn("led_color", mood_data, mood_name)
            self.assertIn("sound", mood_data, mood_name)

    def test_touch_zones_trigger_expected_mood_and_event_payload(self):
        for zone, expected_mood in TOUCH_MOOD.items():
            self.bus.publish("pet/input/touch", {"zone": zone, "timestamp": time.time()})
            self.wait()
            expected = MOODS[expected_mood]
            last_event = self.emotion_events[-1]
            state = self.hal.get_state()
            self.assertEqual(self.emotion.current_mood, expected_mood)
            self.assertEqual(state["face"], expected["face"])
            self.assertEqual(state["led_color"], expected["led_color"])
            self.assertEqual(state["led_mode"], "static")
            self.assertEqual(last_event["mood"], expected_mood)
            self.assertEqual(last_event["face"], expected["face"])
            self.assertEqual(last_event["led_color"], expected["led_color"])
            self.assertEqual(last_event["sound"], expected["sound"])
            self.assertEqual(last_event["triggered_by"], zone)
            self.assertEqual(last_event["energy"], 0.6)

    def test_unknown_touch_does_not_change_mood(self):
        before = self.emotion.current_mood
        self.bus.publish("pet/input/touch", {"zone": "unknown"})
        self.wait()
        self.assertEqual(self.emotion.current_mood, before)
        self.assertEqual(self.emotion_events, [])

    def test_repeating_same_mood_does_not_emit_duplicate_event(self):
        self.bus.publish("pet/input/touch", {"zone": "head"})
        self.wait()
        first_count = len(self.emotion_events)
        self.bus.publish("pet/input/touch", {"zone": "back"})
        self.wait()
        self.assertEqual(self.emotion.current_mood, "happy")
        self.assertEqual(len(self.emotion_events), first_count)

    def test_keyboard_cycle_visits_moods(self):
        seen = []
        ordered_moods = list(MOODS.keys())
        for _ in range(len(ordered_moods)):
            self.bus.publish("pet/input/keyboard", {"action": "cycle_mood"})
            self.wait()
            seen.append(self.emotion.current_mood)
        neutral_index = ordered_moods.index("neutral")
        expected = ordered_moods[neutral_index + 1 :] + ordered_moods[: neutral_index + 1]
        self.assertEqual(seen, expected)

    def test_idle_transitions_to_bored_then_sleepy(self):
        self.assertEqual(self.emotion.current_mood, "neutral")
        self.bus.publish("pet/system/tick", {"timestamp": time.time()})
        self.wait(0.20)
        self.bus.publish("pet/system/tick", {"timestamp": time.time()})
        self.wait()
        self.assertEqual(self.emotion.current_mood, "bored")

        self.wait(0.18)
        self.bus.publish("pet/system/tick", {"timestamp": time.time()})
        self.wait()
        self.assertEqual(self.emotion.current_mood, "sleepy")

    def test_touch_resets_idle_timer(self):
        before = time.time() - 100
        self.emotion.last_interaction_time = before
        self.emotion._on_touch("pet/input/touch", {"zone": "head"})
        self.assertEqual(self.emotion.current_mood, "happy")
        self.assertGreater(self.emotion.last_interaction_time, before)

    def test_stopped_engine_ignores_idle_tick(self):
        self.emotion.stop()
        self.wait(0.18)
        self.bus.publish("pet/system/tick", {"timestamp": time.time()})
        self.wait()
        self.assertEqual(self.emotion.current_mood, "neutral")

    def test_current_mood_is_restored_from_memory(self):
        self.bus.publish("pet/input/touch", {"zone": "chin"})
        self.wait()
        restored = EmotionEngine(self.bus, self.hal, self.memory, self.config)
        try:
            self.assertEqual(restored.current_mood, "love")
        finally:
            restored.stop()

    def test_idle_tick_emits_tick_events(self):
        idle = IdleTick(self.bus, self.config)
        ticks = []
        self.bus.subscribe("pet/system/tick", lambda topic, data: ticks.append(data))
        idle.start()
        try:
            time.sleep(1.15)
        finally:
            idle.stop()
        self.assertGreaterEqual(len(ticks), 1)
        self.assertIn("timestamp", ticks[0])
        self.assertIn("tick_count", ticks[0])

    def test_sound_engine_preloads_and_plays_cached_sounds(self):
        with mock.patch("plugins.sound.engine.SOUND_AVAILABLE", True), \
             mock.patch("plugins.sound.engine.pygame.mixer.init"), \
             mock.patch("plugins.sound.engine.pygame.mixer.quit"), \
             mock.patch("plugins.sound.engine.pygame.sndarray.make_sound") as make_sound:
            mock_sound = mock.Mock()
            make_sound.return_value = mock_sound
            engine = SoundEngine(self.bus, self.hal, self.memory, self.config)
            engine.start()
            try:
                self.assertIn("startup", engine._sound_cache)
                self.bus.publish("pet/sound/play", {"name": "startup"})
                self.wait()
                self.assertTrue(make_sound.called)
                self.assertTrue(mock_sound.play.called)
            finally:
                engine.stop()

    def test_emotion_event_plays_mapped_sound_and_updates_hal(self):
        with mock.patch("plugins.sound.engine.SOUND_AVAILABLE", True), \
             mock.patch("plugins.sound.engine.pygame.mixer.init"), \
             mock.patch("plugins.sound.engine.pygame.mixer.quit"), \
             mock.patch("plugins.sound.engine.pygame.sndarray.make_sound") as make_sound:
            mock_sound = mock.Mock()
            make_sound.return_value = mock_sound
            engine = SoundEngine(self.bus, self.hal, self.memory, self.config)
            engine.start()
            try:
                with mock.patch.object(self.hal, "play_sound") as hal_play_sound:
                    self.bus.publish("pet/emotion/changed", {"mood": "happy", "sound": "chirp"})
                    self.wait()
                    hal_play_sound.assert_called_once_with("chirp")
                    self.assertTrue(mock_sound.play.called)
            finally:
                engine.stop()

    def test_unknown_sound_name_does_not_attempt_playback(self):
        with mock.patch("plugins.sound.engine.SOUND_AVAILABLE", True), \
             mock.patch("plugins.sound.engine.pygame.mixer.init"), \
             mock.patch("plugins.sound.engine.pygame.mixer.quit"), \
             mock.patch("plugins.sound.engine.pygame.sndarray.make_sound") as make_sound:
            engine = SoundEngine(self.bus, self.hal, self.memory, self.config)
            engine.start()
            try:
                make_sound.reset_mock()
                self.bus.publish("pet/sound/play", {"name": "missing"})
                self.wait()
                make_sound.assert_not_called()
            finally:
                engine.stop()

    def test_audio_disabled_mode_short_circuits_playback(self):
        with mock.patch("plugins.sound.engine.SOUND_AVAILABLE", True), \
             mock.patch("plugins.sound.engine.pygame.mixer.init"), \
             mock.patch("plugins.sound.engine.pygame.mixer.quit"), \
             mock.patch("plugins.sound.engine.pygame.sndarray.make_sound") as make_sound:
            engine = SoundEngine(self.bus, self.hal, self.memory, self.config)
            engine.audio_enabled = False
            engine.start()
            try:
                make_sound.reset_mock()
                self.bus.publish("pet/sound/play", {"name": "startup"})
                self.wait()
                make_sound.assert_not_called()
            finally:
                engine.stop()

    def test_renderer_uses_hal_state_on_startup(self):
        self.hal.set_face("sleepy")
        renderer = SimpleRenderer(self.bus, self.hal, self.memory, self.config)
        with mock.patch.object(renderer, "_render_loop", return_value=None):
            renderer.start()
        try:
            self.assertEqual(renderer.current_mood, "sleepy")
        finally:
            renderer.stop()

    def test_renderer_updates_when_emotion_event_arrives(self):
        renderer = SimpleRenderer(self.bus, self.hal, self.memory, self.config)
        renderer._on_emotion_changed("pet/emotion/changed", {"mood": "angry"})
        self.assertEqual(renderer.current_mood, "angry")
        self.assertEqual(self.hal.get_state()["face"], "angry")

    def test_input_mapping_covers_all_touch_zones_and_known_actions(self):
        mapped_touch_zones = {
            payload["zone"]
            for topic, payload in KEY_MAPPINGS.values()
            if topic == "pet/input/touch"
        }
        self.assertEqual(mapped_touch_zones, set(TOUCH_MOOD.keys()))
        self.assertEqual(KEY_MAPPINGS["m"][1]["action"], "cycle_mood")
        self.assertEqual(KEY_MAPPINGS["t"][1]["action"], "test_sound")
        self.assertEqual(KEY_MAPPINGS["q"][0], "pet/system/quit")

    def test_input_simulator_publishes_touch_with_timestamp(self):
        input_sim = InputSimulator(self.bus)
        received = []
        self.bus.subscribe("pet/input/touch", lambda topic, data: received.append(data))
        input_sim._handle_key("h")
        self.wait()
        self.assertEqual(received[0]["zone"], "head")
        self.assertIn("timestamp", received[0])

    def test_input_simulator_quit_is_only_published_once(self):
        input_sim = InputSimulator(self.bus)
        received = []
        self.bus.subscribe("pet/system/quit", lambda topic, data: received.append(data))
        input_sim._handle_key("q")
        input_sim._handle_key("q")
        self.wait()
        self.assertEqual(len(received), 1)

    def test_input_simulator_ignores_unknown_keys(self):
        input_sim = InputSimulator(self.bus)
        received = []
        self.bus.subscribe("pet/input/touch", lambda topic, data: received.append(data))
        input_sim._handle_key("z")
        self.wait()
        self.assertEqual(received, [])

    def test_input_simulator_text_command_mode_submits_as_speech(self):
        input_sim = InputSimulator(self.bus)
        speech_events = []
        transcript_events = []
        self.bus.subscribe("pet/input/speech", lambda topic, data: speech_events.append(data))
        self.bus.subscribe("pet/voice/transcript", lambda topic, data: transcript_events.append(data))
        input_sim._handle_key("/")
        input_sim._handle_key("O")
        input_sim._handle_key("p")
        input_sim._handle_key("e")
        input_sim._handle_key("n")
        input_sim._handle_key(" ")
        for ch in "TextEdit":
            input_sim._handle_key(ch)
        input_sim._handle_key("\n")
        self.wait()
        self.assertTrue(speech_events)
        self.assertTrue(transcript_events)
        self.assertEqual(speech_events[-1]["text"], "Open TextEdit")
        self.assertEqual(speech_events[-1]["source"], "manual_text")


class TestAIAndVoiceStack(BaseOverallTest):
    def setUp(self):
        super().setUp()
        self.memory_manager = MemoryManager(self.bus, self.hal, self.memory, self.config)
        self.memory_manager.start()
        self.bus._memory_manager = self.memory_manager

    def tearDown(self):
        self.memory_manager.stop()
        super().tearDown()

    def test_memory_manager_caps_context_to_recent_history(self):
        for i in range(10):
            self.bus.publish("pet/input/speech", {"text": f"msg-{i}", "confidence": 1.0})
        self.wait()
        context = self.memory_manager.get_context()
        self.assertNotIn("msg-0", context)
        self.assertNotIn("msg-1", context)
        self.assertIn("msg-2", context)
        self.assertIn("msg-9", context)

    def test_memory_manager_uses_configured_name_and_keeps_facts(self):
        self.bus.publish("pet/input/speech", {"text": "My name is Alice", "confidence": 1.0})
        self.bus.publish("pet/input/speech", {"text": "I like robotics", "confidence": 1.0})
        self.wait()
        self.assertEqual(self.memory.recall("facts", "name"), "krishna")
        self.assertEqual(self.memory.recall("facts", "likes"), "robotics")

        reloaded = MemoryManager(self.bus, self.hal, self.memory, self.config)
        try:
            reloaded.start()
            context = reloaded.get_context()
            self.assertIn("krishna", context)
            self.assertIn("robotics", context)
        finally:
            reloaded.stop()

    def test_ai_brain_groq_payload_uses_configured_model_and_limits_output(self):
        brain = AIBrain(self.bus, self.hal, self.memory, self.config)
        fake_response = mock.Mock()
        fake_response.raise_for_status.return_value = None
        fake_response.json.return_value = {
            "choices": [{"message": {"content": '{"text":"ok"}'}}]
        }
        captured = {}

        def fake_post(url, **kwargs):
            captured["url"] = url
            captured["kwargs"] = kwargs
            return fake_response

        with mock.patch.object(brain_module.requests, "post", side_effect=fake_post):
            result = brain._groq_inference("hello")

        self.assertEqual(result, '{"text":"ok"}')
        self.assertEqual(captured["url"], brain_module.GROQ_CHAT_COMPLETIONS_URL)
        payload = captured["kwargs"]["json"]
        self.assertEqual(payload["model"], self.config["ai"]["groq_model"])
        self.assertEqual(payload["max_tokens"], 120)
        self.assertFalse(payload["stream"])
        self.assertIn("temperature", payload)
        self.assertEqual(captured["kwargs"]["headers"]["Authorization"], "Bearer test-groq-key")

    def test_ai_brain_offline_mode_uses_ollama(self):
        offline_config = json.loads(json.dumps(self.config))
        offline_config["ai"]["mode"] = "offline"
        brain = AIBrain(self.bus, self.hal, self.memory, offline_config)
        events = []
        self.bus.subscribe("pet/ai/response", lambda topic, data: events.append(data))
        with mock.patch.object(brain, "_start_ollama_server", return_value=True), \
             mock.patch.object(brain, "_ollama_inference", return_value='{"text":"ok"}') as ollama_call, \
             mock.patch.object(brain_module.requests, "post") as mocked_post:
            brain._process_ai_request("hello")
            self.wait(0.3)
        mocked_post.assert_not_called()
        ollama_call.assert_called_once()
        self.assertTrue(events)
        self.assertEqual(events[-1]["text"], "ok")

    def test_ai_brain_ollama_payload_uses_configured_model_and_host(self):
        brain = AIBrain(self.bus, self.hal, self.memory, self.config)
        fake_response = mock.Mock()
        fake_response.raise_for_status.return_value = None
        fake_response.json.return_value = {
            "response": '{"text":"ok"}'
        }
        captured = {}

        def fake_post(url, **kwargs):
            captured["url"] = url
            captured["kwargs"] = kwargs
            return fake_response

        with mock.patch.object(brain, "_start_ollama_server", return_value=True), \
             mock.patch.object(brain_module.requests, "post", side_effect=fake_post):
            result = brain._ollama_inference("hello")

        self.assertEqual(result, '{"text":"ok"}')
        self.assertEqual(captured["url"], f"{self.config['ai']['ollama_host']}/api/generate")
        payload = captured["kwargs"]["json"]
        self.assertEqual(payload["model"], self.config["ai"]["ollama_model"])
        self.assertFalse(payload["stream"])
        self.assertEqual(payload["options"]["num_predict"], 96)
        self.assertEqual(payload["options"]["num_ctx"], 1024)
        self.assertEqual(payload["options"]["keep_alive"], self.config["ai"]["ollama_keep_alive"])
        self.assertEqual(payload["options"]["temperature"], self.config["ai"]["ollama_temperature"])

    def test_ai_brain_resolves_phi3_model_from_ollama_tags(self):
        brain = AIBrain(self.bus, self.hal, self.memory, self.config)
        with mock.patch.object(
            brain_module.requests,
            "get",
            return_value=mock.Mock(
                status_code=200,
                json=mock.Mock(return_value={
                    "models": [{"name": "phi3:latest"}]
                }),
            ),
        ):
            self.assertEqual(brain._resolve_ollama_model(force_refresh=True), "phi3:latest")

    def test_ai_brain_prefers_faster_phi3_mini_when_available(self):
        fast_config = json.loads(json.dumps(self.config))
        fast_config["ai"]["ollama_model"] = "phi3:mini"
        brain = AIBrain(self.bus, self.hal, self.memory, fast_config)
        with mock.patch.object(
            brain_module.requests,
            "get",
            return_value=mock.Mock(
                status_code=200,
                json=mock.Mock(return_value={
                    "models": [{"name": "phi3:mini"}, {"name": "phi3:latest"}]
                }),
            ),
        ):
            self.assertEqual(brain._resolve_ollama_model(force_refresh=True), "phi3:mini")

    def test_ai_brain_ollama_health_requires_ok_status(self):
        brain = AIBrain(self.bus, self.hal, self.memory, self.config)
        with mock.patch.object(brain_module.requests, "get") as mocked_get:
            mocked_get.return_value = mock.Mock(status_code=404)
            self.assertFalse(brain._ollama_available())
            mocked_get.return_value = mock.Mock(status_code=200)
            self.assertTrue(brain._ollama_available())

    def test_ai_brain_auto_mode_uses_groq_when_available(self):
        auto_config = json.loads(json.dumps(self.config))
        auto_config["ai"]["mode"] = "auto"
        brain = AIBrain(self.bus, self.hal, self.memory, auto_config)
        with mock.patch.object(brain, "_groq_available", return_value=True), \
             mock.patch.object(brain, "_groq_inference", return_value='{"text":"ok"}') as groq_call, \
             mock.patch.object(brain, "_ollama_inference") as ollama_call:
            brain._process_ai_request("hello")
            self.wait(0.3)
        groq_call.assert_called_once()
        ollama_call.assert_not_called()

    def test_ai_brain_auto_mode_falls_back_when_groq_unavailable(self):
        auto_config = json.loads(json.dumps(self.config))
        auto_config["ai"]["mode"] = "auto"
        brain = AIBrain(self.bus, self.hal, self.memory, auto_config)
        events = []
        self.bus.subscribe("pet/ai/response", lambda topic, data: events.append(data))
        with mock.patch.object(brain, "_groq_available", return_value=False), \
             mock.patch.object(brain, "_start_ollama_server", return_value=True), \
             mock.patch.object(brain, "_ollama_inference", return_value='{"text":"ok"}') as ollama_call, \
             mock.patch.object(brain, "_groq_inference") as groq_call:
            brain._process_ai_request("hello")
            self.wait(0.3)
        groq_call.assert_not_called()
        ollama_call.assert_called_once()
        self.assertTrue(events)
        self.assertEqual(events[-1]["text"], "ok")

    def test_ai_brain_prefers_environment_api_key(self):
        env_config = json.loads(json.dumps(self.config))
        env_config["ai"]["groq_api_key"] = ""
        with mock.patch.dict(os.environ, {"GROQ_API_KEY": "env-groq-key"}, clear=False):
            brain = AIBrain(self.bus, self.hal, self.memory, env_config)
        self.assertEqual(brain.groq_api_key, "env-groq-key")

    def test_ai_brain_uses_configured_pet_name_in_prompt(self):
        brain = AIBrain(self.bus, self.hal, self.memory, self.config)
        with mock.patch.object(brain, "_groq_inference", return_value='{"text":"ok"}') as groq_call:
            brain._process_ai_request("hello")
        prompt = groq_call.call_args.args[0]
        self.assertIn("You are Mochi", prompt)

    def test_ai_brain_invalid_json_falls_back(self):
        brain = AIBrain(self.bus, self.hal, self.memory, self.config)
        events = []
        self.bus.subscribe("pet/ai/response", lambda topic, data: events.append(data))
        with mock.patch.object(brain, "_groq_inference", return_value="not-json"):
            brain._process_ai_request("hello")
            self.wait(0.3)
        self.assertGreaterEqual(len(events), 1)
        self.assertEqual(events[-1]["intent"], "system")
        self.assertEqual(events[-1]["emotion_suggestion"], "neutral")
        self.assertTrue(events[-1]["text"])

    def test_ai_brain_validates_actions_and_normalizes_fields(self):
        brain = AIBrain(self.bus, self.hal, self.memory, self.config)
        responses = []
        actions = []
        self.bus.subscribe("pet/ai/response", lambda topic, data: responses.append(data))
        self.bus.subscribe("pet/ai/action", lambda topic, data: actions.append(data))
        payload = (
            '{"text":"Hi there","intent":"greeting","emotion_suggestion":"neutral to positive",'
            '"actions":[{"step":2,"type":"remember_fact","key":"name","value":"Krishna"},'
            '{"step":1,"type":"set_mood","value":"curious"},'
            '{"step":3,"type":"open_app","target":"TextEdit"},'
            '{"step":4,"type":"type_text","text":"hello"},{"type":"unsupported"}]}'
        )
        with mock.patch.object(brain, "_groq_inference", return_value=payload):
            brain._process_ai_request("hello")
            self.wait(0.3)
        self.assertGreaterEqual(len(responses), 1)
        self.assertEqual(responses[-1]["intent"], "social")
        self.assertIn(responses[-1]["emotion_suggestion"], {"happy", "neutral"})
        self.assertEqual(len(actions), 1)
        self.assertIn("task_id", actions[0])
        self.assertEqual([item["type"] for item in actions[0]["actions"]], [
            "set_mood",
            "remember_fact",
            "open_app",
            "type_text",
        ])
        self.assertEqual(self.memory.recall("facts", "name"), "Krishna")

    def test_ai_question_requests_follow_up_listen(self):
        brain = AIBrain(self.bus, self.hal, self.memory, self.config)
        speaks = []
        self.bus.subscribe("pet/speak/say", lambda topic, data: speaks.append(data))
        with mock.patch.object(
            brain,
            "_groq_inference",
            return_value='{"text":"How are you?","intent":"question","emotion_suggestion":"thinking"}',
        ):
            brain._process_ai_request("hello")
            self.wait(0.3)
        self.assertTrue(speaks)
        self.assertTrue(speaks[-1]["listen_after"])

    def test_tts_fallback_prints_text(self):
        tts = TextToSpeech(self.bus, self.hal, self.memory, self.config)
        with mock.patch.object(tts_module, "PIPER_AVAILABLE", False), \
             mock.patch("builtins.print") as mocked_print:
            tts._system_tts = None
            tts.start()
            try:
                self.bus.publish("pet/speak/say", {"text": "hello", "emotion": "happy"})
                self.wait(0.25)
            finally:
                tts.stop()
        mocked_print.assert_called()

    def test_tts_requests_followup_reply(self):
        followups = []
        self.bus.subscribe("pet/voice/listen_for_reply", lambda topic, data: followups.append(data))
        tts = TextToSpeech(self.bus, self.hal, self.memory, self.config)
        with mock.patch.object(tts_module, "PIPER_AVAILABLE", False), \
             mock.patch.object(tts, "_print_text"):
            tts.start()
            try:
                self.bus.publish(
                    "pet/speak/say",
                    {"text": "How are you?", "emotion": "happy", "listen_after": True},
                )
                self.wait(0.25)
            finally:
                tts.stop()
        self.assertTrue(followups)

    def test_tts_stop_publishes_state(self):
        tts = TextToSpeech(self.bus, self.hal, self.memory, self.config)
        states = []
        self.bus.subscribe("pet/voice/tts_state", lambda topic, data: states.append(data["state"]))
        tts.start()
        try:
            self.bus.publish("pet/speak/stop", {})
            self.wait(0.2)
        finally:
            tts.stop()
        self.assertIn("stopped", states)

    def test_stt_wake_triggers_record_path(self):
        fake_model = mock.Mock()
        fake_recorder = mock.Mock(frame_length=1024)
        fake_recorder.start.return_value = None
        fake_recorder.stop.return_value = None
        fake_recorder.delete.return_value = None
        fake_recorder.read.return_value = [0] * 1024
        with mock.patch.object(stt_module, "WHISPER_AVAILABLE", True), \
             mock.patch.object(stt_module, "WhisperModel", return_value=fake_model), \
             mock.patch.object(stt_module, "PvRecorder", return_value=fake_recorder), \
             mock.patch.object(stt_module, "pyaudio", None):
            stt = SpeechToText(self.bus, self.hal, self.memory, self.config)
        with mock.patch.object(stt, "_record_and_transcribe") as record:
            stt.start()
            try:
                self.wait(0.05)
                self.bus.publish("pet/input/wake_word", {"source": "keyboard"})
                self.wait(0.1)
            finally:
                stt.stop()
        record.assert_called_once()

    def test_stt_followup_reply_triggers_record_path(self):
        fake_model = mock.Mock()
        fake_recorder = mock.Mock(frame_length=1024)
        fake_recorder.start.return_value = None
        fake_recorder.stop.return_value = None
        fake_recorder.delete.return_value = None
        fake_recorder.read.return_value = [0] * 1024
        with mock.patch.object(stt_module, "WHISPER_AVAILABLE", True), \
             mock.patch.object(stt_module, "WhisperModel", return_value=fake_model), \
             mock.patch.object(stt_module, "PvRecorder", return_value=fake_recorder), \
             mock.patch.object(stt_module, "pyaudio", None):
            stt = SpeechToText(self.bus, self.hal, self.memory, self.config)
        with mock.patch.object(stt, "_record_and_transcribe") as record:
            stt.start()
            try:
                self.wait(0.05)
                self.bus.publish("pet/voice/listen_for_reply", {"source": "tts"})
                self.wait(0.1)
            finally:
                stt.stop()
        record.assert_called_once()

    def test_stt_transcribe_uses_beam_size_one(self):
        fake_model = mock.Mock()
        fake_model.transcribe.return_value = ([SimpleNamespace(text="hello"), SimpleNamespace(text="world")], None)
        fake_recorder = mock.Mock(frame_length=1024)
        fake_recorder.start.return_value = None
        fake_recorder.stop.return_value = None
        fake_recorder.delete.return_value = None
        fake_recorder.read.return_value = [0] * 1024
        with mock.patch.object(stt_module, "WHISPER_AVAILABLE", True), \
             mock.patch.object(stt_module, "WhisperModel", return_value=fake_model), \
             mock.patch.object(stt_module, "PvRecorder", return_value=fake_recorder), \
             mock.patch.object(stt_module, "pyaudio", None):
            stt = SpeechToText(self.bus, self.hal, self.memory, self.config)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
            path = handle.name
        try:
            self.assertEqual(stt.transcribe(path), "hello world")
            fake_model.transcribe.assert_called_once_with(path, beam_size=1)
        finally:
            os.unlink(path)
            stt.stop()

    def test_stt_emits_transcript_event(self):
        fake_model = mock.Mock()
        fake_model.transcribe.return_value = ([SimpleNamespace(text="hello gui")], None)
        fake_recorder = mock.Mock(frame_length=1024)
        fake_recorder.start.return_value = None
        fake_recorder.stop.return_value = None
        fake_recorder.delete.return_value = None
        fake_recorder.read.return_value = [0] * 1024
        with mock.patch.object(stt_module, "WHISPER_AVAILABLE", True), \
             mock.patch.object(stt_module, "WhisperModel", return_value=fake_model), \
             mock.patch.object(stt_module, "PvRecorder", return_value=fake_recorder), \
             mock.patch.object(stt_module, "pyaudio", None):
            stt = SpeechToText(self.bus, self.hal, self.memory, self.config)

        events = []
        self.bus.subscribe("pet/voice/transcript", lambda topic, data: events.append(data))
        try:
            stt._record_and_transcribe()
            self.wait(0.2)
        finally:
            stt.stop()

        self.assertTrue(events)
        self.assertEqual(events[-1]["text"], "hello gui")
        self.assertEqual(events[-1]["source"], "microphone")

    def test_wake_mode_selects_whisper_without_porcupine(self):
        fake_recorder = mock.Mock(frame_length=1024)
        fake_recorder.start.return_value = None
        fake_recorder.stop.return_value = None
        fake_recorder.delete.return_value = None
        fake_recorder.read.return_value = [0] * 1024
        with mock.patch.object(wake_module, "PORCUPINE_AVAILABLE", False), \
             mock.patch.object(wake_module, "WHISPER_WAKE_AVAILABLE", True), \
             mock.patch.object(wake_module, "PvRecorder", return_value=fake_recorder):
            detector = WakeWordDetector(self.bus, self.hal, self.memory, self.config)
        self.assertEqual(detector._mode, "whisper")
        detector.stop()

    def test_wake_phrase_match_and_publish(self):
        detector = WakeWordDetector(self.bus, self.hal, self.memory, self.config)
        events = []
        self.bus.subscribe("pet/input/wake_word", lambda topic, data: events.append(data))
        detector.wake_word = "hello"
        self.assertTrue(detector._contains_wake_phrase("hello there friend"))
        self.assertFalse(detector._contains_wake_phrase("good morning"))
        detector._publish_wake("mic", "hello there friend")
        self.wait()
        self.assertEqual(events[-1]["source"], "mic")
        self.assertEqual(events[-1]["wake_word"], "hello")
        detector.stop()

    def test_wake_capture_and_transcribe_returns_transcript(self):
        fake_model = mock.Mock()
        fake_model.transcribe.return_value = ([SimpleNamespace(text="hello world")], None)
        fake_recorder = mock.Mock(frame_length=1024)
        fake_recorder.start.return_value = None
        fake_recorder.stop.return_value = None
        fake_recorder.delete.return_value = None
        fake_recorder.read.return_value = [0] * 1024
        with mock.patch.object(wake_module, "PORCUPINE_AVAILABLE", False), \
             mock.patch.object(wake_module, "WHISPER_WAKE_AVAILABLE", True), \
             mock.patch.object(wake_module, "WhisperModel", return_value=fake_model), \
             mock.patch.object(wake_module, "PvRecorder", return_value=fake_recorder):
            detector = WakeWordDetector(self.bus, self.hal, self.memory, self.config)
            result = detector._capture_and_transcribe_phrase()
        self.assertEqual(result, "hello world")
        fake_model.transcribe.assert_called_once()
        detector.stop()

    def test_wake_stop_tolerates_missing_stream(self):
        detector = WakeWordDetector(self.bus, self.hal, self.memory, self.config)
        detector.audio_stream = None
        detector.porcupine = None
        detector.stop()

    def test_wake_detection_pauses_while_tts_is_speaking(self):
        detector = WakeWordDetector(self.bus, self.hal, self.memory, self.config)
        self.assertFalse(detector._paused.is_set())

        detector._on_tts_state("pet/voice/tts_state", {"state": "speaking"})
        self.assertTrue(detector._paused.is_set())

        detector._on_tts_state("pet/voice/tts_state", {"state": "idle"})
        self.assertFalse(detector._paused.is_set())

        detector.stop()

    def test_wake_detection_stays_paused_during_followup_listening(self):
        detector = WakeWordDetector(self.bus, self.hal, self.memory, self.config)
        self.bus._voice_followup_active = True
        detector._on_tts_state("pet/voice/tts_state", {"state": "idle"})
        self.assertTrue(detector._paused.is_set())

        self.bus._voice_followup_active = False
        detector._on_tts_state("pet/voice/tts_state", {"state": "idle"})
        self.assertFalse(detector._paused.is_set())

        detector.stop()


if __name__ == "__main__":
    unittest.main(verbosity=2)
