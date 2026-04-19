import logging
import threading
import time
import sys

from core.utils import unwrap_event_payload

logger = logging.getLogger(__name__)

class SimpleRenderer:
    def __init__(self, bus, hal, memory, config):
        self.bus = bus
        self.hal = hal
        self.memory = memory
        self.config = config
        self.assistant_mode = bool(config.get("personality", {}).get("assistant_mode", False))
        self._running = True
        self.current_mood = "neutral"
        self._render_thread = None

    def start(self):
        self.bus.subscribe("pet/emotion/changed", self._on_emotion_changed)
        self.current_mood = self.hal.get_state().get("face", self.current_mood)
        self._render_thread = threading.Thread(target=self._render_loop)
        self._render_thread.daemon = True
        self._render_thread.start()
        logger.info("Simple renderer started")

    def stop(self):
        self._running = False
        if self._render_thread:
            self._render_thread.join(timeout=1)

    def _on_emotion_changed(self, topic, data):
        data = unwrap_event_payload(data)
        self.current_mood = data.get("mood", "neutral")
        # Also update HAL for tracking
        self.hal.set_face(self.current_mood)

    def _render_loop(self):
        target_fps = 10
        frame_time = 1.0 / target_fps
        while self._running:
            start = time.perf_counter()
            self._draw_status()
            elapsed = time.perf_counter() - start
            sleep_time = frame_time - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

    def _draw_status(self):
        # Print a single line, overwriting the previous one
        if not getattr(sys.stdout, "isatty", lambda: False)():
            return
        label = "Assistant" if self.assistant_mode else "E-Pet"
        print(f"\r{label}: {self.current_mood} " + " " * 10, end="", flush=True)
