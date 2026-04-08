import logging
import time
from typing import Dict, Any

from core.utils import unwrap_event_payload

logger = logging.getLogger(__name__)

# Mood definitions
MOODS = {
    "happy": {"face": "happy", "led_color": "yellow", "sound": "chirp"},
    "excited": {"face": "excited", "led_color": "orange", "sound": "excited_trill"},
    "love": {"face": "love", "led_color": "pink", "sound": "purr"},
    "curious": {"face": "curious", "led_color": "cyan", "sound": "thinking_hum"},
    "thinking": {"face": "thinking", "led_color": "blue", "sound": "thinking_hum"},
    "neutral": {"face": "neutral", "led_color": "white", "sound": "boop"},
    "bored": {"face": "bored", "led_color": "gray", "sound": "squeak"},
    "sleepy": {"face": "sleepy", "led_color": "dark_blue", "sound": "sad_whimper"},
    "sad": {"face": "sad", "led_color": "blue", "sound": "sad_whimper"},
    "nervous": {"face": "nervous", "led_color": "purple", "sound": "alert"},
    "angry": {"face": "angry", "led_color": "red", "sound": "alert"},
    "surprised": {"face": "surprised", "led_color": "magenta", "sound": "chime"},
}

# Touch zone → mood mapping
TOUCH_MOOD = {
    "head": "happy",
    "chin": "love",
    "back": "happy",
    "belly": "excited",
    "poke": "angry",
    "shake": "nervous",
    "hold": "sleepy",
    "double_tap": "surprised",
}

class EmotionEngine:
    def __init__(self, bus, hal, memory, config):
        self.bus = bus
        self.hal = hal
        self.memory = memory
        self.config = config

        self.current_mood = None
        self.energy_level = config.get("personality", {}).get("energy", 0.6)
        self.last_interaction_time = time.time()
        self._running = True

        # Idle thresholds from config
        self.bored_after = config.get("idle", {}).get("bored_after", 120)
        self.sleepy_after = config.get("idle", {}).get("sleepy_after", 300)

        # Restore saved mood
        saved = memory.get("current_mood")
        if saved and saved in MOODS:
            self.current_mood = saved
        else:
            self.current_mood = "neutral"

        # Initialise hardware
        self._apply_mood()

    def start(self):
        # Subscribe to events
        self.bus.subscribe("pet/input/touch", self._on_touch)
        self.bus.subscribe("pet/system/tick", self._on_tick)
        self.bus.subscribe("pet/input/keyboard", self._on_debug)
        self.bus.subscribe("pet/ai/response", self._on_ai_response)
        self.bus.subscribe("pet/ai/action", self._on_ai_action)
        logger.info("Emotion: ready")

    def stop(self):
        self._running = False

    def _on_touch(self, topic, data):
        data = unwrap_event_payload(data)
        zone = data.get("zone")
        if zone in TOUCH_MOOD:
            new_mood = TOUCH_MOOD[zone]
            self._change_mood(new_mood, triggered_by=zone)
        self.last_interaction_time = time.time()

    def _on_tick(self, topic, data):
        data = unwrap_event_payload(data)
        if not self._running:
            return
        now = time.time()
        idle_time = now - self.last_interaction_time
        if idle_time >= self.sleepy_after and self.current_mood != "sleepy":
            self._change_mood("sleepy", triggered_by="idle")
        elif idle_time >= self.bored_after and self.current_mood not in ("bored", "sleepy"):
            self._change_mood("bored", triggered_by="idle")

    def _on_debug(self, topic, data):
        data = unwrap_event_payload(data)
        action = data.get("action")
        if action == "cycle_mood":
            moods = list(MOODS.keys())
            idx = moods.index(self.current_mood)
            next_mood = moods[(idx + 1) % len(moods)]
            self._change_mood(next_mood, triggered_by="debug")
        elif action == "test_sound":
            self.bus.publish("pet/sound/play", {"name": "notification"})

    def _on_ai_response(self, topic, data):
        data = unwrap_event_payload(data)
        suggestion = data.get("emotion_suggestion")
        if suggestion and suggestion in MOODS:
            self._change_mood(suggestion, triggered_by="ai")

    def _on_ai_action(self, topic, data):
        data = unwrap_event_payload(data)
        actions = data.get("actions") if isinstance(data, dict) else None
        if actions is None:
            actions = [data] if isinstance(data, dict) else []
        for action in actions:
            if not isinstance(action, dict):
                continue
            if action.get("type") == "set_mood":
                mood = action.get("value")
                if mood in MOODS:
                    self._change_mood(mood, triggered_by="ai_action")

    def _change_mood(self, new_mood, triggered_by=""):
        if new_mood == self.current_mood:
            return
        self.current_mood = new_mood
        # Persist
        self.memory.set("current_mood", new_mood)
        # Apply hardware
        self._apply_mood()
        # Publish event
        mood_data = {
            "mood": new_mood,
            "face": MOODS[new_mood]["face"],
            "led_color": MOODS[new_mood]["led_color"],
            "sound": MOODS[new_mood]["sound"],
            "energy": self.energy_level,
            "triggered_by": triggered_by,
        }
        self.bus.publish("pet/emotion/changed", mood_data)
        logger.info(f"Emotion: {new_mood} (source: {triggered_by})")

    def _apply_mood(self):
        mood = MOODS[self.current_mood]
        self.hal.set_face(mood["face"])
        self.hal.set_led(mood["led_color"], "static")
        # Don't play sound directly; sound engine will handle via pet/emotion/changed

def start(bus, hal, memory, config):
    engine = EmotionEngine(bus, hal, memory, config)
    engine.start()
    # Keep reference to prevent GC
    bus._emotion_engine = engine
    return engine
