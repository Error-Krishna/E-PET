import os
import sys
import tempfile
import time
import unittest
from unittest import mock
import types

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.event_bus import EventBus
from core.config_validation import normalize_and_validate_config
from core.hal import HALSimulator
from core.memory import Memory
from plugins.ai.brain import AIBrain
from plugins.ai.memory_manager import MemoryManager
from plugins.emotion.engine import EmotionEngine
from plugins.voice.stt import SpeechToText
from plugins.voice.tts import TextToSpeech
from plugins.voice.wake import WakeWordDetector

import tests.final_validation_suite as final_validation_suite
import tests.test_v2_integration as test_v2_integration


class BaseFinalV2EdgeTest(unittest.TestCase):
    def setUp(self):
        self.memory_file = tempfile.NamedTemporaryFile(delete=False)
        self.memory_file.close()

        self.bus = EventBus()
        self.hal = HALSimulator(debug=False)
        self.memory = Memory(self.memory_file.name)
        self.config = {
            "personality": {"energy": 0.6},
            "idle": {"bored_after": 120, "sleepy_after": 300},
            "voice": {
                "enabled": True,
                "wake_word": "hey pip",
                "tts_model": "en_US-lessac-medium",
                "piper_path": "piper",
            },
            "ai": {
                "enabled": True,
                "mode": "local",
                "model": "phi3",
                "api_key": "",
                "local_url": "http://localhost:11434/api/generate",
                "online_url": "https://api.openai.com/v1/chat/completions",
                "online_model": "gpt-3.5-turbo",
            },
            "memory": {"max_history": 3},
        }

    def tearDown(self):
        self.memory.close()
        os.unlink(self.memory_file.name)

    @staticmethod
    def wait_for_async_work(delay=0.15):
        time.sleep(delay)


class TestAdditionalV2Edges(BaseFinalV2EdgeTest):
    def test_emotion_ignores_unknown_ai_suggestion(self):
        engine = EmotionEngine(self.bus, self.hal, self.memory, self.config)
        engine.start()
        events = []
        self.bus.subscribe("pet/emotion/changed", lambda topic, data: events.append(data))
        try:
            self.bus.publish(
                "pet/ai/response",
                {"text": "x", "intent": "social", "emotion_suggestion": "nonexistent"},
            )
            self.wait_for_async_work()
            self.assertEqual(engine.current_mood, "neutral")
            self.assertEqual(events, [])
        finally:
            engine.stop()

    def test_memory_manager_caps_history(self):
        manager = MemoryManager(self.bus, self.hal, self.memory, self.config)
        manager.start()
        try:
            for i in range(5):
                self.bus.publish("pet/input/speech", {"text": f"msg-{i}", "confidence": 1.0})
            self.wait_for_async_work()
            context = manager.get_context()
            self.assertNotIn("msg-0", context)
            self.assertNotIn("msg-1", context)
            self.assertIn("msg-2", context)
            self.assertIn("msg-3", context)
            self.assertIn("msg-4", context)
        finally:
            manager.stop()

    def test_memory_manager_extracts_and_persists_facts_and_history(self):
        manager = MemoryManager(self.bus, self.hal, self.memory, self.config)
        manager.start()
        try:
            self.bus.publish("pet/input/speech", {"text": "My name is Alice", "confidence": 1.0})
            self.bus.publish("pet/input/speech", {"text": "I like robotics", "confidence": 1.0})
            self.wait_for_async_work()

            self.assertEqual(self.memory.recall("facts", "name"), "Alice")
            self.assertEqual(self.memory.recall("facts", "likes"), "robotics")

            reloaded = MemoryManager(self.bus, self.hal, self.memory, self.config)
            reloaded.start()
            try:
                context = reloaded.get_context()
                self.assertIn("Alice", context)
                self.assertIn("robotics", context)
            finally:
                reloaded.stop()
        finally:
            manager.stop()

    def test_ai_brain_invalid_json_falls_back(self):
        manager = MemoryManager(self.bus, self.hal, self.memory, self.config)
        manager.start()
        self.bus._memory_manager = manager
        brain = AIBrain(self.bus, self.hal, self.memory, self.config)
        events = []
        self.bus.subscribe("pet/ai/response", lambda topic, data: events.append(data))
        try:
            with mock.patch.object(brain, "_local_inference", return_value="not-json"):
                brain.start()
                self.bus.publish("pet/input/speech", {"text": "hello", "confidence": 1.0})
                self.wait_for_async_work(0.35)
            self.assertGreaterEqual(len(events), 1)
            self.assertEqual(events[-1]["intent"], "system")
            self.assertEqual(events[-1]["emotion_suggestion"], "neutral")
            self.assertTrue(events[-1]["text"])
        finally:
            brain.stop()
            manager.stop()

    def test_ai_brain_validates_actions_and_normalizes_fields(self):
        manager = MemoryManager(self.bus, self.hal, self.memory, self.config)
        manager.start()
        self.bus._memory_manager = manager
        brain = AIBrain(self.bus, self.hal, self.memory, self.config)
        responses = []
        actions = []
        self.bus.subscribe("pet/ai/response", lambda topic, data: responses.append(data))
        self.bus.subscribe("pet/ai/action", lambda topic, data: actions.append(data))
        try:
            payload = (
                '{"text":"Hi there","intent":"greeting","emotion_suggestion":"neutral to positive",'
                '"actions":[{"type":"remember_fact","key":"name","value":"Krishna"},'
                '{"type":"set_mood","value":"curious"},{"type":"unsupported"}]}'
            )
            with mock.patch.object(brain, "_local_inference", return_value=payload):
                brain.start()
                self.bus.publish("pet/input/speech", {"text": "hello", "confidence": 1.0})
                self.wait_for_async_work(0.35)

            self.assertGreaterEqual(len(responses), 1)
            self.assertEqual(responses[-1]["intent"], "social")
            self.assertIn(responses[-1]["emotion_suggestion"], {"happy", "neutral"})
            self.assertEqual(len(actions), 2)
            self.assertEqual(actions[0]["type"], "remember_fact")
            self.assertEqual(actions[1]["type"], "set_mood")
        finally:
            brain.stop()
            manager.stop()

    def test_voice_plugin_start_sets_bus_references(self):
        from plugins.voice import plugin as voice_plugin

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

    def test_ai_plugin_start_sets_bus_references(self):
        from plugins.ai import plugin as ai_plugin

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
        from plugins.ai import plugin as ai_plugin

        cfg = dict(self.config)
        cfg["ai"] = {"enabled": False}
        with mock.patch.object(ai_plugin, "MemoryManager") as manager_cls, \
             mock.patch.object(ai_plugin, "AIBrain") as brain_cls:
            ai_plugin.start(self.bus, self.hal, self.memory, cfg)
        manager_cls.assert_not_called()
        brain_cls.assert_not_called()

    def test_stt_stop_tolerates_placeholder_audio(self):
        stt = SpeechToText(self.bus, self.hal, self.memory, self.config)
        stt.audio = object()
        stt.stop()

    def test_wake_stop_tolerates_missing_stream(self):
        wake = WakeWordDetector(self.bus, self.hal, self.memory, self.config)
        wake.audio_stream = None
        wake.porcupine = None
        wake.stop()

    def test_wake_uses_configured_porcupine_keyword(self):
        import plugins.voice.wake as wake_module

        cfg = dict(self.config)
        cfg["voice"] = dict(self.config["voice"])
        cfg["voice"]["porcupine_access_key"] = "test-key"
        cfg["voice"]["wake_keyword"] = "computer"
        fake_porcupine = mock.Mock(sample_rate=16000, frame_length=512)

        with mock.patch.object(wake_module, "PORCUPINE_AVAILABLE", True), \
             mock.patch.object(
                 wake_module,
                 "pvporcupine",
                 types.SimpleNamespace(create=mock.Mock(return_value=fake_porcupine)),
                 create=True,
             ), \
             mock.patch.dict(
                 "sys.modules",
                 {
                     "pyaudio": types.SimpleNamespace(
                         PyAudio=mock.Mock(return_value=mock.Mock(open=mock.Mock(return_value=mock.Mock()))),
                         paInt16=8,
                      )
                 },
             ):
            create_mock = wake_module.pvporcupine.create
            detector = WakeWordDetector(self.bus, self.hal, self.memory, cfg)

        create_mock.assert_called_once()
        self.assertEqual(create_mock.call_args.kwargs["access_key"], "test-key")
        self.assertEqual(create_mock.call_args.kwargs["keywords"], ["computer"])
        detector.stop()

    def test_wake_uses_whisper_phrase_mode_without_porcupine(self):
        import plugins.voice.wake as wake_module

        cfg = dict(self.config)
        cfg["voice"] = dict(self.config["voice"])
        cfg["voice"]["wake_mode"] = "whisper"
        cfg["voice"]["wake_word"] = "hey pip"

        with mock.patch.object(wake_module, "PORCUPINE_AVAILABLE", False), \
             mock.patch.object(wake_module, "WHISPER_WAKE_AVAILABLE", True), \
             mock.patch.dict(
                 "sys.modules",
                 {
                     "pyaudio": types.SimpleNamespace(
                         PyAudio=mock.Mock(return_value=mock.Mock()),
                         paInt16=8,
                     )
                 },
             ):
            detector = WakeWordDetector(self.bus, self.hal, self.memory, cfg)

        self.assertEqual(detector._mode, "whisper")
        detector.stop()

    def test_wake_phrase_match_publishes_event(self):
        detector = WakeWordDetector(self.bus, self.hal, self.memory, self.config)
        events = []
        self.bus.subscribe("pet/input/wake_word", lambda topic, data: events.append(data))

        detector.wake_word = "hey pip"
        self.assertTrue(detector._contains_wake_phrase("hello hey pip how are you"))
        self.assertFalse(detector._contains_wake_phrase("hello there"))

        detector._publish_wake("mic", "hey pip how are you")
        self.wait_for_async_work()

        self.assertEqual(events[-1]["source"], "mic")
        self.assertEqual(events[-1]["wake_word"], "hey pip")
        detector.stop()

    def test_tts_fallback_prints_text(self):
        tts = TextToSpeech(self.bus, self.hal, self.memory, self.config)
        with mock.patch("plugins.voice.tts.PIPER_AVAILABLE", False), \
             mock.patch("builtins.print") as mocked_print:
            tts.start()
            self.bus.publish("pet/speak/say", {"text": "hello", "emotion": "happy"})
            self.wait_for_async_work(0.25)
            tts.stop()
        mocked_print.assert_called()

    def test_tts_stop_publishes_state(self):
        tts = TextToSpeech(self.bus, self.hal, self.memory, self.config)
        states = []
        self.bus.subscribe("pet/voice/tts_state", lambda topic, data: states.append(data["state"]))
        tts.start()
        try:
            self.bus.publish("pet/speak/stop", {})
            self.wait_for_async_work(0.2)
        finally:
            tts.stop()
        self.assertIn("stopped", states)

    def test_config_validation_normalizes_defaults(self):
        config = normalize_and_validate_config({"plugins": {"enabled": ["emotion", "emotion"]}})
        self.assertEqual(config["plugins"]["enabled"], ["emotion"])
        self.assertEqual(config["logging"]["level"], "INFO")
        self.assertEqual(config["voice"]["whisper_model"], "tiny")
        self.assertEqual(config["voice"]["wake_mode"], "auto")


def build_suite():
    loader = unittest.defaultTestLoader
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromModule(final_validation_suite))
    suite.addTests(loader.loadTestsFromModule(test_v2_integration))
    suite.addTests(loader.loadTestsFromTestCase(TestAdditionalV2Edges))
    return suite


if __name__ == "__main__":
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(build_suite())
    raise SystemExit(0 if result.wasSuccessful() else 1)
