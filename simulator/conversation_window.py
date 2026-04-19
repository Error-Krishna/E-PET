from __future__ import annotations

import logging
import queue
import time
from dataclasses import dataclass
from typing import Any

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


@dataclass
class ConversationWindow:
    """Temporary plain-text conversation viewer.

    Runs in a separate process so macOS can create its own main-thread window.
    """

    event_queue: Any
    size: tuple[int, int] = (520, 360)

    def __post_init__(self):
        self._available = bool(PYGAME_AVAILABLE)
        if not self._available:
            logger.warning("GUI: conversation window unavailable: %s", PYGAME_IMPORT_ERROR)
            return

        self._running = False
        self._quit_requested = False
        self._window = None
        self._clock = None
        self._input_text = ""
        self._response_text = ""
        self._status = "waiting for conversation"
        self._last_update = 0.0

    def run(self):
        if not self._available:
            return
        pygame.init()
        try:
            self._window = pygame.display.set_mode(self.size)
        except Exception as exc:
            raise RuntimeError(f"Unable to open the conversation window: {exc}") from exc
        pygame.display.set_caption("E-Pet Conversation")

        self._clock = pygame.time.Clock()
        self._running = True
        logger.info("GUI: conversation window ready")

        while self._running and not self._quit_requested:
            self._handle_events()
            self._drain_event_queue()
            self._draw()
            self._clock.tick(24)

        self._cleanup()

    def stop(self):
        self._quit_requested = True
        self._running = False

    def _handle_events(self):
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.stop()

    def _drain_event_queue(self):
        while True:
            try:
                event = self.event_queue.get_nowait()
            except queue.Empty:
                break
            self._apply_event(event)

    def _apply_event(self, event: dict[str, Any]):
        kind = event.get("kind")
        data = event.get("data") or {}
        text = str(data.get("text", "")).strip()
        if not text:
            if kind == "quit":
                self.stop()
            return

        self._last_update = time.monotonic()
        if kind == "input":
            self._input_text = text
            self._status = "listening"
        elif kind == "response":
            self._response_text = text
            self._status = "responded"
        elif kind == "quit":
            self.stop()

    def _draw(self):
        if self._window is None:
            return

        bg = (13, 18, 28)
        panel = (20, 28, 41)
        accent = (96, 165, 250)
        label = (226, 232, 240)
        body = (203, 213, 225)
        dim = (148, 163, 184)

        self._window.fill(bg)
        pygame.draw.rect(self._window, panel, pygame.Rect(12, 12, self.size[0] - 24, self.size[1] - 24), border_radius=18)
        pygame.draw.rect(self._window, accent, pygame.Rect(12, 12, self.size[0] - 24, self.size[1] - 24), width=2, border_radius=18)

        title_font = pygame.font.SysFont("Helvetica", 17, bold=True)
        label_font = pygame.font.SysFont("Helvetica", 12, bold=True)
        body_font = pygame.font.SysFont("Helvetica", 15)
        tiny_font = pygame.font.SysFont("Helvetica", 11)

        self._window.blit(title_font.render("Conversation", True, label), (28, 24))
        self._window.blit(tiny_font.render(self._status, True, dim), (28, 48))

        y = 82
        if self._input_text:
            self._window.blit(label_font.render("INPUT", True, accent), (28, y))
            y += 22
            for line in self._wrap_text(self._input_text, body_font, self.size[0] - 56):
                self._window.blit(body_font.render(line, True, body), (28, y))
                y += 22
            y += 14

        if self._response_text:
            self._window.blit(label_font.render("RESPONSE", True, accent), (28, y))
            y += 22
            for line in self._wrap_text(self._response_text, body_font, self.size[0] - 56):
                self._window.blit(body_font.render(line, True, body), (28, y))
                y += 22
            y += 14

        if not self._input_text and not self._response_text:
            self._window.blit(body_font.render("Waiting for speech and reply...", True, dim), (28, 110))

        footer = tiny_font.render("temporary test window", True, dim)
        self._window.blit(footer, (28, self.size[1] - 30))

        pygame.display.flip()

    @staticmethod
    def _wrap_text(text: str, font, max_width: int) -> list[str]:
        words = text.split()
        if not words:
            return [""]
        lines: list[str] = []
        current = words[0]
        for word in words[1:]:
            candidate = f"{current} {word}"
            if font.size(candidate)[0] <= max_width:
                current = candidate
            else:
                lines.append(current)
                current = word
        lines.append(current)
        return lines

    def _cleanup(self):
        self._running = False
        try:
            pygame.quit()
        except Exception:
            pass


def run_conversation_window(event_queue, size=(520, 360)):
    window = ConversationWindow(event_queue=event_queue, size=size)
    window.run()
