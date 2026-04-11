from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from epet_gui.ipc.bridge import read_json_file


class StatePollWorker(QObject):
    stateChanged = Signal(dict)
    statusChanged = Signal(str)
    finished = Signal()

    def __init__(self, state_path: Path, interval_ms: int = 1000, parent=None):
        super().__init__(parent)
        self.state_path = Path(state_path)
        self.interval_ms = interval_ms
        self._timer = None

    @Slot()
    def start(self):
        self._timer = QTimer(self)
        self._timer.setInterval(self.interval_ms)
        self._timer.timeout.connect(self._poll)
        self._timer.start()
        self._poll()

    @Slot()
    def stop(self):
        if self._timer is not None:
            self._timer.stop()
        self.finished.emit()

    def _poll(self):
        data = read_json_file(self.state_path)
        if data is None:
            self.statusChanged.emit("E-Pet is not running")
            return
        self.stateChanged.emit(data)
        self.statusChanged.emit("State synced")


class PetStatePanel(QWidget):
    stateUpdated = Signal(dict)

    def __init__(self, state_path: Path, on_command, parent=None):
        super().__init__(parent)
        self.state_path = Path(state_path)
        self.on_command = on_command
        self._state = {}

        self._status_badge = QLabel("Stopped")
        self._mood_value = QLabel("neutral")
        self._ai_value = QLabel("auto")
        self._voice_value = QLabel("idle")
        self._uptime_value = QLabel("0s")
        self._last_response_value = QLabel("-")
        self._last_speech_value = QLabel("-")
        self._facts_value = QLabel("0")
        self._conversation_value = QLabel("0")

        self._status_badge.setObjectName("statusBadge")

        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.addWidget(self._build_summary())
        layout.addWidget(self._build_stats())
        layout.addStretch(1)

        self._poll_thread = QThread(self)
        self._poll_worker = StatePollWorker(self.state_path)
        self._poll_worker.moveToThread(self._poll_thread)
        self._poll_thread.started.connect(self._poll_worker.start)
        self._poll_worker.stateChanged.connect(self._apply_state, Qt.QueuedConnection)
        self._poll_thread.start()

    def closeEvent(self, event):
        self._poll_worker.stop()
        self._poll_thread.quit()
        self._poll_thread.wait(1000)
        super().closeEvent(event)

    def _build_summary(self):
        box = QGroupBox("Dashboard")
        grid = QGridLayout(box)
        grid.addWidget(QLabel("Pet Status"), 0, 0)
        grid.addWidget(self._status_badge, 0, 1)
        grid.addWidget(QLabel("Current Mood"), 1, 0)
        grid.addWidget(self._mood_value, 1, 1)
        grid.addWidget(QLabel("AI Mode"), 2, 0)
        grid.addWidget(self._ai_value, 2, 1)
        grid.addWidget(QLabel("Voice"), 3, 0)
        grid.addWidget(self._voice_value, 3, 1)
        grid.addWidget(QLabel("Uptime"), 4, 0)
        grid.addWidget(self._uptime_value, 4, 1)
        return box

    def _build_stats(self):
        box = QGroupBox("Memory")
        grid = QGridLayout(box)
        grid.addWidget(QLabel("Facts Stored"), 0, 0)
        grid.addWidget(self._facts_value, 0, 1)
        grid.addWidget(QLabel("Conversation Turns"), 1, 0)
        grid.addWidget(self._conversation_value, 1, 1)
        grid.addWidget(QLabel("Last User Speech"), 2, 0)
        grid.addWidget(self._last_speech_value, 2, 1)
        grid.addWidget(QLabel("Last AI Response"), 3, 0)
        grid.addWidget(self._last_response_value, 3, 1)

        button_row = QHBoxLayout()
        for label, command, args in [
            ("Speak Something", "speak", {"text": "Hello from the control center!"}),
            ("Trigger Wake", "trigger_wake", {"source": "gui"}),
            ("Change Mood", "set_mood", {"mood": "curious"}),
        ]:
            button = QPushButton(label)
            button.clicked.connect(lambda _, c=command, a=args: self.on_command({"command": c, "args": a}))
            button_row.addWidget(button)
        grid.addLayout(button_row, 4, 0, 1, 2)
        return box

    @Slot(dict)
    def _apply_state(self, state: dict):
        self._state = state or {}
        self.stateUpdated.emit(self._state)
        running = bool(self._state.get("running"))
        self._status_badge.setText("Running" if running else "Stopped")
        self._mood_value.setText(str(self._state.get("mood", "neutral")))
        self._ai_value.setText(f"{self._state.get('ai_mode', 'auto')} ({self._state.get('ai_backend', 'n/a')})")
        self._voice_value.setText(str(self._state.get("voice_state", "idle")))
        self._uptime_value.setText(f"{int(self._state.get('uptime_seconds', 0))}s")
        self._last_speech_value.setText(_truncate(self._state.get("last_speech", "-")))
        self._last_response_value.setText(_truncate(self._state.get("last_response", "-")))
        memory = self._state.get("memory", {}) if isinstance(self._state.get("memory"), dict) else {}
        self._facts_value.setText(str(memory.get("facts_total", 0)))
        self._conversation_value.setText(str(memory.get("conversation_count", 0)))


def _truncate(value, limit=200):
    text = str(value or "-")
    return text if len(text) <= limit else text[: limit - 1] + "…"
