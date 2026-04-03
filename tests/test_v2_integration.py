import os
import sys
import time
import unittest
from unittest import mock

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.event_bus import EventBus
from core.hal import HALSimulator
from core.memory import Memory
from plugins.ai.brain import AIBrain
from plugins.ai.memory_manager import MemoryManager
from plugins.emotion.engine import EmotionEngine
from plugins.voice.stt import SpeechToText
from plugins.voice.tts import TextToSpeech
from plugins.voice.wake import WakeWordDetector


class TestV2Integration(unittest.TestCase):
    def setUp(self):
        self.bus = EventBus()
        self.hal = HALSimulator(debug=False)
        self.memory = Memory(":memory:")
        self.config = {
            "personality": {"energy": 0.6},
            "idle": {"bored_after": 120, "sleepy_after": 300},
            "voice": {"enabled": True},
            "ai": {"enabled": True, "mode": "local"},
            "memory": {"max_history": 20},
        }
        self.emotion = EmotionEngine(self.bus, self.hal, self.memory, self.config)
        self.emotion.start()
        self.memory_manager = MemoryManager(self.bus, self.hal, self.memory, self.config)
        self.memory_manager.start()
        self.bus._memory_manager = self.memory_manager
        self.brain = AIBrain(self.bus, self.hal, self.memory, self.config)
        self.stt = SpeechToText(self.bus, self.hal, self.memory, self.config)
        self.tts = TextToSpeech(self.bus, self.hal, self.memory, self.config)
        self.wake = WakeWordDetector(self.bus, self.hal, self.memory, self.config)
        self.events = []
        self.bus.subscribe("pet/input/speech", lambda t, d: self.events.append(("speech", d)))
        self.bus.subscribe("pet/ai/response", lambda t, d: self.events.append(("ai_response", d)))
        self.bus.subscribe("pet/speak/say", lambda t, d: self.events.append(("speak", d)))
        self.bus.subscribe("pet/emotion/changed", lambda t, d: self.events.append(("emotion", d)))

    def tearDown(self):
        self.emotion.stop()
        self.memory_manager.stop()
        self.brain.stop()
        self.stt.stop()
        self.tts.stop()
        self.wake.stop()
        self.memory.close()

    def test_wake_triggers_stt_path(self):
        with mock.patch.object(self.stt, "_record_and_transcribe") as record:
            self.stt.model = object()
            self.stt.audio = object()
            self.stt.start()
            self.bus.publish("pet/input/wake_word", {"source": "keyboard"})
            time.sleep(0.1)
            record.assert_called_once()

    def test_speech_to_ai_response(self):
        self.brain.start()
        with mock.patch.object(
            self.brain,
            "_local_inference",
            return_value='{"text":"Pip here!","intent":"question","emotion_suggestion":"thinking"}',
        ):
            self.bus.publish("pet/input/speech", {"text": "What is your name?", "confidence": 1.0})
            time.sleep(0.4)
        ai_responses = [e[1] for e in self.events if e[0] == "ai_response"]
        self.assertTrue(len(ai_responses) > 0)
        self.assertIn("text", ai_responses[0])
        self.assertIn("intent", ai_responses[0])
        self.assertIn("emotion_suggestion", ai_responses[0])

    def test_tts_fallback(self):
        with mock.patch("plugins.voice.tts.PIPER_AVAILABLE", False):
            with mock.patch("builtins.print") as mocked_print:
                self.tts.start()
                self.bus.publish("pet/speak/say", {"text": "Hello", "emotion": "happy"})
                time.sleep(0.2)
                mocked_print.assert_called()

    def test_memory_manager(self):
        self.bus.publish("pet/input/speech", {"text": "My name is Alice", "confidence": 1.0})
        time.sleep(0.1)
        self.bus.publish(
            "pet/ai/response",
            {
                "text": "Nice to meet you, Alice",
                "intent": "social",
                "emotion_suggestion": "happy",
            },
        )
        time.sleep(0.1)
        context = self.memory_manager.get_context()
        self.assertIn("Alice", context)

    def test_emotion_from_ai(self):
        self.bus.publish(
            "pet/ai/response",
            {"text": "Hello", "intent": "social", "emotion_suggestion": "happy"},
        )
        time.sleep(0.1)
        emotion_events = [e for e in self.events if e[0] == "emotion"]
        self.assertTrue(len(emotion_events) > 0)
        self.assertEqual(emotion_events[-1][1]["mood"], "happy")


if __name__ == "__main__":
    unittest.main()
