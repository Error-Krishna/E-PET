from __future__ import annotations

import sys
import time
from pathlib import Path

from PySide6.QtCore import QProcess, QTimer, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from epet_gui.config.schema import PLUGIN_NAMES


class PetControls(QWidget):
    def __init__(self, project_root: Path, get_config, save_config, is_running, send_command, parent=None):
        super().__init__(parent)
        self.project_root = Path(project_root)
        self.get_config = get_config
        self.save_config = save_config
        self.is_running = is_running
        self.send_command = send_command
        self._started_at = 0.0
        self._buffer = []

        self._status = QLabel("Stopped")
        self._pid = QLabel("-")
        self._runtime = QLabel("0s")
        self._plugin_checks: dict[str, QCheckBox] = {}

        self._process = QProcess(self)
        self._process.readyReadStandardOutput.connect(self._drain_stdout)
        self._process.readyReadStandardError.connect(self._drain_stderr)
        self._process.started.connect(self._on_started)
        self._process.finished.connect(self._on_finished)

        top = QGroupBox("Pet Process")
        top_layout = QFormLayout(top)
        top_layout.addRow("Status", self._status)
        top_layout.addRow("PID", self._pid)
        top_layout.addRow("Runtime", self._runtime)

        buttons = QHBoxLayout()
        for label, action in [
            ("Start E-Pet", self.start_pet),
            ("Stop E-Pet", self.stop_pet),
            ("Restart", self.restart_pet),
            ("Force Kill", self.kill_pet),
        ]:
            button = QPushButton(label)
            button.clicked.connect(action)
            buttons.addWidget(button)
        top_layout.addRow(buttons)

        plugin_box = QGroupBox("Plugin Toggles")
        plugin_layout = QHBoxLayout(plugin_box)
        for name in PLUGIN_NAMES:
            check = QCheckBox(name)
            check.toggled.connect(lambda checked, plugin=name: self._toggle_plugin(plugin, checked))
            self._plugin_checks[name] = check
            plugin_layout.addWidget(check)
        plugin_layout.addStretch(1)

        mood_box = QGroupBox("Mood Control")
        mood_layout = QHBoxLayout(mood_box)
        for mood in ["happy", "sad", "curious", "bored", "angry", "neutral"]:
            button = QPushButton(mood)
            button.clicked.connect(lambda _, m=mood: self.send_command({"command": "set_mood", "args": {"mood": m}}))
            mood_layout.addWidget(button)

        event_box = QGroupBox("Event Injector")
        event_layout = QVBoxLayout(event_box)
        self._topic = QLineEdit()
        self._topic.setPlaceholderText("pet/system/example")
        self._payload = QTextEdit()
        self._payload.setPlaceholderText('{"hello": "world"}')
        publish_btn = QPushButton("Publish Event")
        publish_btn.clicked.connect(self.publish_event)
        event_layout.addWidget(self._topic)
        event_layout.addWidget(self._payload)
        event_layout.addWidget(publish_btn)

        layout = QVBoxLayout(self)
        layout.addWidget(top)
        layout.addWidget(plugin_box)
        layout.addWidget(mood_box)
        layout.addWidget(event_box, 1)

        self._sync_plugin_checks()
        self._tick = QTimer(self)
        self._tick.setInterval(1000)
        self._tick.timeout.connect(self._refresh_runtime)
        self._tick.start()

    def start_pet(self):
        if self._process.state() != QProcess.NotRunning:
            return
        main_path = self.project_root / "main.py"
        self._process.setProgram(sys.executable)
        self._process.setArguments(["-u", str(main_path)])
        self._process.setWorkingDirectory(str(self.project_root))
        self._process.start()

    def stop_pet(self):
        self.send_command({"command": "quit", "args": {"source": "gui"}})
        if self._process.state() != QProcess.NotRunning:
            self._process.terminate()
            if not self._process.waitForFinished(1500):
                self._process.kill()

    def restart_pet(self):
        self.stop_pet()
        QTimer.singleShot(400, self.start_pet)

    def kill_pet(self):
        if self._process.state() != QProcess.NotRunning:
            self._process.kill()

    def publish_event(self):
        topic = self._topic.text().strip()
        payload_text = self._payload.toPlainText().strip() or "{}"
        try:
            import json

            payload = json.loads(payload_text)
            self.send_command({"command": "publish_event", "args": {"topic": topic, "data": payload}})
        except Exception as exc:
            QMessageBox.critical(self, "Publish Failed", str(exc))

    def _toggle_plugin(self, plugin: str, checked: bool):
        config = self.get_config()
        enabled = set(config.get("plugins", {}).get("enabled", []))
        if checked:
            enabled.add(plugin)
        else:
            enabled.discard(plugin)
        config.setdefault("plugins", {})
        config["plugins"]["enabled"] = [name for name in PLUGIN_NAMES if name in enabled]
        self.save_config(config)

    def _sync_plugin_checks(self):
        config = self.get_config()
        enabled = set(config.get("plugins", {}).get("enabled", []))
        for name, check in self._plugin_checks.items():
            check.blockSignals(True)
            check.setChecked(name in enabled)
            check.blockSignals(False)

    def _drain_stdout(self):
        data = bytes(self._process.readAllStandardOutput()).decode("utf-8", "replace")
        if data:
            self._buffer.extend(data.splitlines())
            self._buffer = self._buffer[-1000:]

    def _drain_stderr(self):
        data = bytes(self._process.readAllStandardError()).decode("utf-8", "replace")
        if data:
            self._buffer.extend(data.splitlines())
            self._buffer = self._buffer[-1000:]

    def _on_started(self):
        self._started_at = time.time()
        self._status.setText("Running")
        self._pid.setText(str(self._process.processId()))

    def _on_finished(self, exit_code, exit_status):
        self._status.setText("Stopped" if exit_code == 0 else f"Exited ({exit_code})")
        self._pid.setText("-")
        if exit_code != 0:
            tail = "\n".join(self._buffer[-20:])
            QMessageBox.warning(self, "E-Pet Stopped", f"The pet exited unexpectedly.\n\nLast output:\n{tail}")

    def _refresh_runtime(self):
        if self._process.state() == QProcess.Running and self._started_at:
            self._runtime.setText(f"{int(time.time() - self._started_at)}s")
        else:
            self._runtime.setText("0s")
