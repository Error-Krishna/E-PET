from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass
from typing import Any

from core.utils import unwrap_event_payload

logger = logging.getLogger(__name__)

try:
    import pygame

    PYGAME_AVAILABLE = True
except Exception as exc:  # pragma: no cover - only hit when pygame is missing
    pygame = None
    PYGAME_AVAILABLE = False
    PYGAME_IMPORT_ERROR = exc
else:
    PYGAME_IMPORT_ERROR = None


MOOD_STYLE = {
    "happy": {"eyes": "open", "mouth": "smile", "accent": (249, 168, 37)},
    "excited": {"eyes": "wide", "mouth": "open", "accent": (251, 146, 60)},
    "love": {"eyes": "heart", "mouth": "smile", "accent": (244, 114, 182)},
    "curious": {"eyes": "look_left", "mouth": "small", "accent": (34, 197, 94)},
    "thinking": {"eyes": "half", "mouth": "flat", "accent": (59, 130, 246)},
    "neutral": {"eyes": "open", "mouth": "flat", "accent": (148, 163, 184)},
    "bored": {"eyes": "half", "mouth": "flat", "accent": (100, 116, 139)},
    "sleepy": {"eyes": "closed", "mouth": "tiny", "accent": (96, 165, 250)},
    "sad": {"eyes": "droop", "mouth": "frown", "accent": (96, 165, 250)},
    "nervous": {"eyes": "wide", "mouth": "tiny", "accent": (168, 85, 247)},
    "angry": {"eyes": "angry", "mouth": "frown", "accent": (239, 68, 68)},
    "surprised": {"eyes": "wide", "mouth": "o", "accent": (236, 72, 153)},
}


@dataclass
class FaceWindow:
    """Expression-only pygame face window."""

    bus: Any
    hal: Any
    memory: Any
    config: dict[str, Any]

    def __post_init__(self):
        if not PYGAME_AVAILABLE:
            raise RuntimeError(f"pygame is unavailable for the face window: {PYGAME_IMPORT_ERROR}")

        self._event_queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=128)
        self._quit_requested = threading.Event()
        self._subscriptions_active = False
        self._mood = self._initial_mood()
        self._running = False
        self._screen = None
        self._clock = None
        self._square_size = 420

    def _initial_mood(self) -> str:
        if self.memory is not None:
            stored = self.memory.get("current_mood")
            if stored:
                return stored
        if self.hal is not None:
            return self.hal.get_state().get("face", "neutral")
        return "neutral"

    def start(self):
        if self._subscriptions_active:
            return

        self.bus.subscribe("pet/emotion/changed", self._on_emotion_changed)
        self.bus.subscribe("pet/system/quit", self._on_quit)
        self._subscriptions_active = True

    def run(self):
        self.start()
        pygame.init()
        try:
            self._screen = pygame.display.set_mode((self._square_size, self._square_size))
        except Exception as exc:
            raise RuntimeError(f"Unable to open the face window: {exc}") from exc
        pygame.display.set_caption("E-Pet")
        self._clock = pygame.time.Clock()
        self._running = True
        logger.info("GUI: face window ready")

        while self._running and not self._quit_requested.is_set():
            self._handle_pygame_events()
            self._drain_event_queue()
            self._draw()
            self._clock.tick(30)

        self._cleanup()

    def stop(self):
        self._quit_requested.set()
        self._running = False

    def _handle_pygame_events(self):
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.stop()

    def _draw(self):
        if self._screen is None:
            return

        mood = self._mood
        style = MOOD_STYLE.get(mood, MOOD_STYLE["neutral"])
        bg = {
            "happy": (26, 35, 46),
            "excited": (45, 28, 16),
            "love": (38, 20, 30),
            "curious": (18, 34, 26),
            "thinking": (17, 24, 46),
            "neutral": (15, 23, 42),
            "bored": (24, 27, 34),
            "sleepy": (10, 15, 28),
            "sad": (14, 24, 38),
            "nervous": (31, 18, 42),
            "angry": (45, 18, 18),
            "surprised": (39, 15, 31),
        }.get(mood, (15, 23, 42))

        self._screen.fill(bg)

        title_font = pygame.font.SysFont("Helvetica", 13, bold=True)
        tiny_font = pygame.font.SysFont("Helvetica", 10)
        center_x = self._square_size // 2
        center_y = self._square_size // 2 - 14
        face_rect = pygame.Rect(34, 18, 352, 348)
        pygame.draw.rect(self._screen, (17, 24, 39), face_rect, border_radius=40)
        pygame.draw.rect(self._screen, style["accent"], face_rect, width=4, border_radius=36)

        self._draw_eyes(style["eyes"], center_x, center_y)
        self._draw_mouth(style["mouth"], center_x, center_y)

        title_surface = title_font.render(mood.upper(), True, (226, 232, 240))
        self._screen.blit(title_surface, title_surface.get_rect(center=(center_x, 372)))

        footer = tiny_font.render("Close window to quit", True, (148, 163, 184))
        self._screen.blit(footer, footer.get_rect(center=(center_x, 392)))

        pygame.display.flip()

    def _draw_eyes(self, eye_type: str, center_x: int, center_y: int):
        left = (center_x - 72, center_y - 48)
        right = (center_x + 72, center_y - 48)
        white = (243, 244, 246)
        dark = (17, 24, 39)

        if eye_type == "closed":
            pygame.draw.line(self._screen, white, (left[0] - 20, left[1]), (left[0] + 20, left[1]), 6)
            pygame.draw.line(self._screen, white, (right[0] - 20, right[1]), (right[0] + 20, right[1]), 6)
        elif eye_type == "half":
            pygame.draw.arc(self._screen, white, pygame.Rect(left[0] - 20, left[1] - 12, 40, 28), 3.14159, 6.28318, 5)
            pygame.draw.arc(self._screen, white, pygame.Rect(right[0] - 20, right[1] - 12, 40, 28), 3.14159, 6.28318, 5)
        elif eye_type == "droop":
            pygame.draw.circle(self._screen, white, left, 13)
            pygame.draw.circle(self._screen, white, right, 13)
            pygame.draw.line(self._screen, dark, (left[0] - 6, left[1] + 6), (left[0] + 6, left[1] + 12), 2)
            pygame.draw.line(self._screen, dark, (right[0] - 6, right[1] + 12), (right[0] + 6, right[1] + 6), 2)
        elif eye_type == "angry":
            pygame.draw.circle(self._screen, white, left, 12)
            pygame.draw.circle(self._screen, white, right, 12)
            pygame.draw.line(self._screen, dark, (left[0] - 14, left[1] - 14), (left[0] + 10, left[1] - 4), 4)
            pygame.draw.line(self._screen, dark, (right[0] - 10, right[1] - 4), (right[0] + 14, right[1] - 14), 4)
        elif eye_type == "heart":
            self._draw_heart_eye(left)
            self._draw_heart_eye(right)
        elif eye_type == "wide":
            pygame.draw.circle(self._screen, white, left, 17)
            pygame.draw.circle(self._screen, white, right, 17)
            pygame.draw.circle(self._screen, dark, left, 6)
            pygame.draw.circle(self._screen, dark, right, 6)
        else:
            pygame.draw.circle(self._screen, white, left, 15)
            pygame.draw.circle(self._screen, white, right, 15)
            pygame.draw.circle(self._screen, dark, left, 6)
            pygame.draw.circle(self._screen, dark, right, 6)

    def _draw_heart_eye(self, center: tuple[int, int]):
        x, y = center
        color = (244, 114, 182)
        pygame.draw.circle(self._screen, color, (x - 8, y - 2), 7)
        pygame.draw.circle(self._screen, color, (x + 8, y - 2), 7)
        points = [(x - 14, y - 1), (x, y + 16), (x + 14, y - 1)]
        pygame.draw.polygon(self._screen, color, points)

    def _draw_mouth(self, mouth_type: str, center_x: int, center_y: int):
        dark = (243, 244, 246)
        accent = (17, 24, 39)
        mouth_y = center_y + 66

        if mouth_type == "smile":
            pygame.draw.arc(self._screen, dark, pygame.Rect(center_x - 38, mouth_y - 14, 76, 30), 3.2, 6.1, 5)
        elif mouth_type == "open":
            pygame.draw.ellipse(self._screen, dark, pygame.Rect(center_x - 16, mouth_y - 6, 32, 26))
            pygame.draw.ellipse(self._screen, accent, pygame.Rect(center_x - 11, mouth_y - 1, 22, 16))
        elif mouth_type == "frown":
            pygame.draw.arc(self._screen, dark, pygame.Rect(center_x - 38, mouth_y - 10, 76, 30), 0.2, 2.9, 5)
        elif mouth_type == "tiny":
            pygame.draw.circle(self._screen, dark, (center_x, mouth_y + 3), 5)
        elif mouth_type == "o":
            pygame.draw.circle(self._screen, dark, (center_x, mouth_y + 3), 12, 3)
        elif mouth_type == "flat":
            pygame.draw.line(self._screen, dark, (center_x - 28, mouth_y + 3), (center_x + 28, mouth_y + 3), 5)
        else:
            pygame.draw.line(self._screen, dark, (center_x - 22, mouth_y + 3), (center_x + 22, mouth_y + 3), 4)

    def _enqueue(self, topic: str, data: Any):
        payload = {"topic": topic, "data": data}
        try:
            if self._event_queue.full():
                self._event_queue.get_nowait()
            self._event_queue.put_nowait(payload)
        except queue.Empty:
            self._event_queue.put_nowait(payload)
        except queue.Full:
            pass

    def _on_emotion_changed(self, topic: str, data: Any):
        data = unwrap_event_payload(data)
        self._enqueue(topic, data)

    def _on_quit(self, topic: str, data: Any):
        data = unwrap_event_payload(data)
        self._quit_requested.set()

    def _drain_event_queue(self):
        while True:
            try:
                event = self._event_queue.get_nowait()
            except queue.Empty:
                break
            self._apply_event(event)

    def _apply_event(self, event: dict[str, Any]):
        topic = event.get("topic")
        data = event.get("data") or {}

        if topic == "pet/emotion/changed":
            self._mood = str(data.get("mood", "neutral"))

    def _cleanup(self):
        self._running = False
        try:
            pygame.quit()
        except Exception:
            pass
