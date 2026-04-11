from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget


class PetStatusBarWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = QLabel("Stopped")
        self._ai_mode = QLabel("AI: unknown")
        self._last_event = QLabel("Last event: none")
        self._uptime = QLabel("Uptime: 0s")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 2, 8, 2)
        layout.setSpacing(16)
        for label in (self._running, self._ai_mode, self._last_event, self._uptime):
            label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            layout.addWidget(label)
        layout.addStretch(1)

    def set_running(self, running: bool, healthy: bool = True):
        if running and healthy:
            self._running.setText("Running")
        elif running:
            self._running.setText("Running (warning)")
        else:
            self._running.setText("Stopped")

    def set_ai_mode(self, mode: str):
        self._ai_mode.setText(f"AI: {mode}")

    def set_last_event(self, event: str):
        self._last_event.setText(f"Last event: {event or 'none'}")

    def set_uptime(self, seconds: int):
        self._uptime.setText(f"Uptime: {max(0, int(seconds))}s")

