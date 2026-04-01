import unittest
import time
import threading
from epet.core.event_bus import EventBus
from epet.core.hal import HALSimulator
from epet.core.memory import Memory
from epet.plugins.emotion.engine import EmotionEngine, TOUCH_MOOD
from epet.plugins.sound.engine import SoundEngine
from epet.plugins.idle.engine import IdleTick

class TestV1Integration(unittest.TestCase):
    def setUp(self):
        self.bus = EventBus()
        self.hal = HALSimulator(debug=False)
        self.memory = Memory(":memory:")
        self.config = {
            "personality": {"energy": 0.6},
            "idle": {"bored_after": 1, "sleepy_after": 2}  # short for testing
        }
        self.emotion = EmotionEngine(self.bus, self.hal, self.memory, self.config)
        self.emotion.start()
        self.sound = SoundEngine(self.bus, self.hal, self.memory, {})
        self.sound.start()
        self.idle = IdleTick(self.bus, self.config)
        self.idle.start()
        self.events = []
        self.bus.subscribe("pet/emotion/changed", lambda t,d: self.events.append(("emotion", d)))
        self.bus.subscribe("pet/sound/play", lambda t,d: self.events.append(("sound", d)))

    def tearDown(self):
        self.emotion.stop()
        self.sound.stop()
        self.idle.stop()
        self.memory.close()

    def test_mood_triggers(self):
        for zone, expected_mood in TOUCH_MOOD.items():
            self.bus.publish("pet/input/touch", {"zone": zone})
            time.sleep(0.1)
            # Check last emotion event
            found = False
            for ev in reversed(self.events):
                if ev[0] == "emotion":
                    self.assertEqual(ev[1]["mood"], expected_mood)
                    found = True
                    break
            self.assertTrue(found, f"Did not get emotion for zone {zone}")

    def test_sound_on_mood(self):
        # Trigger a mood change
        self.bus.publish("pet/input/touch", {"zone": "head"})
        time.sleep(0.1)
        # Should have a sound event
        sound_events = [e for e in self.events if e[0] == "sound"]
        self.assertGreaterEqual(len(sound_events), 1)
        self.assertEqual(sound_events[-1][1]["name"], "chirp")  # happy sound

    def test_idle_decay(self):
        # Initially neutral
        self.assertEqual(self.emotion.current_mood, "neutral")
        # Wait for bored threshold (1 sec)
        time.sleep(1.5)
        self.assertEqual(self.emotion.current_mood, "bored")
        # Wait for sleepy (another 1 sec)
        time.sleep(1.5)
        self.assertEqual(self.emotion.current_mood, "sleepy")

    def test_mood_persistence(self):
        # Change mood
        self.bus.publish("pet/input/touch", {"zone": "head"})
        time.sleep(0.1)
        # Restart engine
        new_engine = EmotionEngine(self.bus, self.hal, self.memory, self.config)
        new_engine.start()
        self.assertEqual(new_engine.current_mood, "happy")
        new_engine.stop()

    def test_system_stability(self):
        # Fire 50 rapid random touches
        zones = list(TOUCH_MOOD.keys())
        for _ in range(50):
            zone = zones[_ % len(zones)]
            self.bus.publish("pet/input/touch", {"zone": zone})
        time.sleep(0.5)
        # Should have a final mood and no exceptions
        self.assertIn(self.emotion.current_mood, TOUCH_MOOD.values())
        # Ensure all events processed
        self.assertGreaterEqual(len(self.events), 50)

if __name__ == "__main__":
    unittest.main()