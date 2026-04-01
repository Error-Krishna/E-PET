import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.event_bus import EventBus
from core.hal import HALSimulator
from core.memory import Memory
from core.plugin_loader import PluginLoader
from plugins.emotion.engine import EmotionEngine, MOODS, TOUCH_MOOD
from plugins.idle.engine import IdleTick
from plugins.sound.engine import SoundEngine
from simulator.face_renderer import SimpleRenderer
from simulator.input_sim import InputSimulator, KEY_MAPPINGS


class BaseIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.memory_file = tempfile.NamedTemporaryFile(delete=False)
        self.memory_file.close()

        self.bus = EventBus()
        self.hal = HALSimulator(debug=False)
        self.memory = Memory(self.memory_file.name)
        self.config = {
            "personality": {
                "curiosity": 0.5,
                "energy": 0.6,
                "sociability": 0.5,
            },
            "idle": {
                "bored_after": 0.15,
                "sleepy_after": 0.30,
            },
        }

    def tearDown(self):
        self.memory.close()
        os.unlink(self.memory_file.name)

    @staticmethod
    def wait_for_async_work(delay=0.08):
        time.sleep(delay)


class TestEventBusFinal(BaseIntegrationTest):
    def test_exact_subscription_delivery(self):
        received = []

        def callback(topic, data):
            received.append((topic, data))

        self.bus.subscribe("pet/test/exact", callback)
        self.bus.publish("pet/test/exact", {"ok": True})
        self.wait_for_async_work()

        self.assertEqual(received, [("pet/test/exact", {"ok": True})])

    def test_wildcard_subscription_delivery(self):
        received = []

        def callback(topic, data):
            received.append((topic, data))

        self.bus.subscribe("pet/*", callback)
        self.bus.subscribe("pet/input/*", callback)
        self.bus.publish("pet/input/touch", {"zone": "head"})
        self.bus.publish("pet/status", {"mood": "happy"})
        self.wait_for_async_work()

        topics = [topic for topic, _ in received]
        self.assertEqual(topics.count("pet/input/touch"), 2)
        self.assertEqual(topics.count("pet/status"), 1)

    def test_callback_error_does_not_break_other_subscribers(self):
        calls = []

        def bad_callback(topic, data):
            raise RuntimeError("boom")

        def good_callback(topic, data):
            calls.append((topic, data))

        self.bus.subscribe("pet/test/error", bad_callback)
        self.bus.subscribe("pet/test/error", good_callback)
        self.bus.publish("pet/test/error", {"survived": True})
        self.wait_for_async_work()

        self.assertEqual(calls, [("pet/test/error", {"survived": True})])

    def test_publish_with_no_subscribers_is_safe(self):
        self.bus.publish("pet/test/none", {"unused": True})
        self.wait_for_async_work()


class TestMemoryFinal(BaseIntegrationTest):
    def test_key_value_round_trip_and_overwrite(self):
        self.memory.set("current_mood", "happy")
        self.assertEqual(self.memory.get("current_mood"), "happy")

        self.memory.set("current_mood", "sleepy")
        self.assertEqual(self.memory.get("current_mood"), "sleepy")
        self.assertIsNone(self.memory.get("missing"))

    def test_event_log_is_persisted(self):
        self.memory.log_event("touch", '{"zone": "head"}')
        cursor = self.memory.conn.cursor()
        cursor.execute("SELECT event_type, data FROM events")
        rows = cursor.fetchall()

        self.assertEqual(rows, [("touch", '{"zone": "head"}')])

    def test_categorized_memory_round_trip_and_overwrite(self):
        self.memory.remember("bond", "favorite_zone", "head")
        self.assertEqual(self.memory.recall("bond", "favorite_zone"), "head")

        self.memory.remember("bond", "favorite_zone", "chin")
        self.assertEqual(self.memory.recall("bond", "favorite_zone"), "chin")
        self.assertIsNone(self.memory.recall("bond", "missing"))
        self.assertIsNone(self.memory.recall("other", "favorite_zone"))

    def test_sqlite_data_persists_across_reopen(self):
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


class TestPluginLoaderFinal(BaseIntegrationTest):
    def setUp(self):
        super().setUp()
        self.plugins_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.plugins_dir)
        super().tearDown()

    def write_plugin(self, name, plugin_source, engine_source=None):
        plugin_path = os.path.join(self.plugins_dir, name)
        os.makedirs(plugin_path)

        with open(os.path.join(plugin_path, "plugin.py"), "w", encoding="utf-8") as handle:
            handle.write(plugin_source)

        if engine_source is not None:
            with open(os.path.join(plugin_path, "engine.py"), "w", encoding="utf-8") as handle:
                handle.write(engine_source)

    def test_loader_imports_relative_plugin_engine_and_passes_full_config(self):
        self.write_plugin(
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
        self.wait_for_async_work()

        self.assertEqual(received, [{"energy": 0.6, "bored_after": 0.15}])

    def test_loader_skips_disabled_plugins(self):
        self.write_plugin(
            "disabled_plugin",
            "def start(bus, hal, memory, config):\n    bus.publish('plugin/disabled_should_not_run', {})\n",
        )

        received = []
        self.bus.subscribe(
            "plugin/disabled_should_not_run",
            lambda topic, data: received.append(data),
        )

        loader = PluginLoader(
            [],
            self.bus,
            self.hal,
            self.memory,
            self.config,
            plugins_dir=self.plugins_dir,
        )
        loader.load_plugins()
        self.wait_for_async_work()

        self.assertEqual(received, [])


class TestEmotionEngineFinal(BaseIntegrationTest):
    def setUp(self):
        super().setUp()
        self.engine = EmotionEngine(self.bus, self.hal, self.memory, self.config)
        self.engine.start()
        self.emotion_events = []
        self.bus.subscribe(
            "pet/emotion/changed",
            lambda topic, data: self.emotion_events.append(data),
        )

    def tearDown(self):
        self.engine.stop()
        super().tearDown()

    def test_all_moods_have_complete_metadata(self):
        for mood_name, mood_data in MOODS.items():
            self.assertIn("face", mood_data, mood_name)
            self.assertIn("led_color", mood_data, mood_name)
            self.assertIn("sound", mood_data, mood_name)
            self.assertIsInstance(mood_data["face"], str)
            self.assertIsInstance(mood_data["led_color"], str)
            self.assertIsInstance(mood_data["sound"], str)

    def test_all_touch_zones_trigger_expected_mood_and_event_payload(self):
        for zone, expected_mood in TOUCH_MOOD.items():
            self.bus.publish("pet/input/touch", {"zone": zone, "timestamp": time.time()})
            self.wait_for_async_work()

            state = self.hal.get_state()
            last_event = self.emotion_events[-1]
            expected = MOODS[expected_mood]

            self.assertEqual(self.engine.current_mood, expected_mood)
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
        before = self.engine.current_mood
        self.bus.publish("pet/input/touch", {"zone": "unknown"})
        self.wait_for_async_work()

        self.assertEqual(self.engine.current_mood, before)
        self.assertEqual(self.emotion_events, [])

    def test_repeating_same_mood_does_not_emit_duplicate_event(self):
        self.bus.publish("pet/input/touch", {"zone": "head"})
        self.wait_for_async_work()
        first_event_count = len(self.emotion_events)

        self.bus.publish("pet/input/touch", {"zone": "back"})
        self.wait_for_async_work()

        self.assertEqual(self.engine.current_mood, "happy")
        self.assertEqual(len(self.emotion_events), first_event_count)

    def test_keyboard_cycle_visits_every_mood_once_before_wrapping(self):
        seen = []
        ordered_moods = list(MOODS.keys())

        for _ in range(len(ordered_moods)):
            self.bus.publish("pet/input/keyboard", {"action": "cycle_mood"})
            self.wait_for_async_work()
            seen.append(self.engine.current_mood)

        self.assertEqual(seen, ordered_moods[ordered_moods.index("neutral") + 1 :] + ordered_moods[: ordered_moods.index("neutral") + 1])

    def test_idle_transitions_to_bored_then_sleepy(self):
        self.assertEqual(self.engine.current_mood, "neutral")
        self.bus.publish("pet/system/tick", {"timestamp": time.time()})
        self.wait_for_async_work(0.20)
        self.bus.publish("pet/system/tick", {"timestamp": time.time()})
        self.wait_for_async_work()
        self.assertEqual(self.engine.current_mood, "bored")

        self.wait_for_async_work(0.18)
        self.bus.publish("pet/system/tick", {"timestamp": time.time()})
        self.wait_for_async_work()
        self.assertEqual(self.engine.current_mood, "sleepy")

    def test_touch_resets_idle_timer(self):
        self.wait_for_async_work(0.12)
        self.bus.publish("pet/input/touch", {"zone": "head"})
        self.wait_for_async_work()

        self.wait_for_async_work(0.05)
        self.bus.publish("pet/system/tick", {"timestamp": time.time()})
        self.wait_for_async_work()

        self.assertEqual(self.engine.current_mood, "happy")

    def test_stopped_engine_ignores_idle_tick(self):
        self.engine.stop()
        self.wait_for_async_work(0.18)
        self.bus.publish("pet/system/tick", {"timestamp": time.time()})
        self.wait_for_async_work()

        self.assertEqual(self.engine.current_mood, "neutral")

    def test_current_mood_is_restored_from_memory(self):
        self.bus.publish("pet/input/touch", {"zone": "chin"})
        self.wait_for_async_work()
        restored = EmotionEngine(self.bus, self.hal, self.memory, self.config)
        try:
            self.assertEqual(restored.current_mood, "love")
        finally:
            restored.stop()


class TestIdlePluginFinal(BaseIntegrationTest):
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


class TestSoundEngineFinal(BaseIntegrationTest):
    def setUp(self):
        super().setUp()
        self._patchers = [
            mock.patch("plugins.sound.engine.SOUND_AVAILABLE", True),
            mock.patch("plugins.sound.engine.pygame.mixer.init"),
            mock.patch("plugins.sound.engine.pygame.mixer.quit"),
            mock.patch("plugins.sound.engine.pygame.sndarray.make_sound"),
        ]
        started = [patcher.start() for patcher in self._patchers]
        self.addCleanup(self._stop_patchers)

        self.mock_make_sound = started[3]
        mock_sound = mock.Mock()
        self.mock_make_sound.return_value = mock_sound
        self.mock_play = mock_sound.play

        self.engine = SoundEngine(self.bus, self.hal, self.memory, self.config)
        self.engine.start()

    def _stop_patchers(self):
        for patcher in reversed(self._patchers):
            patcher.stop()

    def tearDown(self):
        self.engine.stop()
        super().tearDown()

    def test_all_declared_sounds_generate_valid_arrays(self):
        for name, generator in self.engine.sounds.items():
            samples = generator()
            self.assertGreater(len(samples), 0, name)
            self.assertTrue(hasattr(samples, "dtype"), name)
            self.assertFalse((samples != samples).any(), name)

    def test_direct_sound_event_plays_requested_sound(self):
        self.bus.publish("pet/sound/play", {"name": "startup"})
        self.wait_for_async_work()
        self.assertTrue(self.mock_make_sound.called)
        self.assertTrue(self.mock_play.called)

    def test_emotion_event_plays_mapped_sound_and_updates_hal(self):
        with mock.patch.object(self.hal, "play_sound") as hal_play_sound:
            self.bus.publish(
                "pet/emotion/changed",
                {"mood": "happy", "sound": "chirp"},
            )
            self.wait_for_async_work()

            hal_play_sound.assert_called_once_with("chirp")
            self.assertTrue(self.mock_make_sound.called)

    def test_unknown_sound_name_does_not_attempt_playback(self):
        self.mock_make_sound.reset_mock()
        self.bus.publish("pet/sound/play", {"name": "missing"})
        self.wait_for_async_work()
        self.mock_make_sound.assert_not_called()

    def test_audio_disabled_mode_short_circuits_playback(self):
        self.engine.audio_enabled = False
        self.mock_make_sound.reset_mock()
        self.bus.publish("pet/sound/play", {"name": "startup"})
        self.wait_for_async_work()
        self.mock_make_sound.assert_not_called()


class TestRendererAndInputFinal(BaseIntegrationTest):
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
        self.wait_for_async_work()

        self.assertEqual(received[0]["zone"], "head")
        self.assertIn("timestamp", received[0])

    def test_input_simulator_quit_is_only_published_once(self):
        input_sim = InputSimulator(self.bus)
        received = []
        self.bus.subscribe("pet/system/quit", lambda topic, data: received.append(data))

        input_sim._handle_key("q")
        input_sim._handle_key("q")
        self.wait_for_async_work()

        self.assertEqual(len(received), 1)

    def test_input_simulator_ignores_unknown_keys(self):
        input_sim = InputSimulator(self.bus)
        received = []
        self.bus.subscribe("pet/input/touch", lambda topic, data: received.append(data))

        input_sim._handle_key("z")
        self.wait_for_async_work()

        self.assertEqual(received, [])


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
