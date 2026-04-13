from __future__ import annotations

import json
from pathlib import Path

import requests
from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from epet_gui.ipc.bridge import resolve_log_file_path


class ProbeWorker(QObject):
    result = Signal(object)
    status = Signal(str)
    finished = Signal()

    def __init__(self, kind: str, payload: dict):
        super().__init__()
        self.kind = kind
        self.payload = payload

    @Slot()
    def run(self):
        try:
            if self.kind == "groq":
                api_key = self.payload["api_key"]
                model = self.payload["model"]
                headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
                body = {
                    "model": model,
                    "messages": [{"role": "user", "content": "Say ok."}],
                    "max_tokens": 16,
                    "temperature": 0.1,
                    "stream": False,
                }
                response = requests.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers=headers,
                    json=body,
                    timeout=5,
                )
                response.raise_for_status()
                self.result.emit({"ok": True, "text": "Groq reachable", "raw": response.json()})
            elif self.kind == "ollama":
                host = self.payload["host"].rstrip("/")
                response = requests.get(f"{host}/api/tags", timeout=5)
                response.raise_for_status()
                self.result.emit({"ok": True, "text": "Ollama reachable", "raw": response.json()})
            else:
                self.result.emit({"ok": False, "text": "Unknown probe"})
        except Exception as exc:
            self.result.emit({"ok": False, "text": str(exc)})
        finally:
            self.finished.emit()


class AIControls(QWidget):
    def __init__(self, get_config, save_config, send_command, parent=None):
        super().__init__(parent)
        self.get_config = get_config
        self.save_config = save_config
        self.send_command = send_command
        self._probe_thread = None
        self._probe_worker = None

        self._mode = QComboBox()
        self._mode.addItems(["auto", "online", "offline"])
        self._groq_key = QLineEdit()
        self._groq_key.setEchoMode(QLineEdit.Password)
        self._groq_model = QLineEdit()
        self._ollama_host = QLineEdit()
        self._ollama_model = QLineEdit()
        self._context = QSpinBox()
        self._prompt = QLineEdit()
        self._response = QTextEdit()
        self._response.setReadOnly(True)
        self._status = QLabel("Idle")

        form = QFormLayout()
        form.addRow("AI Mode", self._mode)
        form.addRow("Groq API Key", self._groq_key)
        form.addRow("Groq Model", self._groq_model)
        form.addRow("Ollama Host", self._ollama_host)
        form.addRow("Ollama Model", self._ollama_model)
        # The control panel intentionally exposes only the single Ollama model
        # knob; the config schema does not have a separate fast-model setting.
        form.addRow("Max Context", self._context)

        groq_probe = QPushButton("Test Groq")
        groq_probe.clicked.connect(self.test_groq)
        ollama_probe = QPushButton("Check Ollama")
        ollama_probe.clicked.connect(self.test_ollama)
        send_prompt = QPushButton("Send to Pet Brain")
        send_prompt.clicked.connect(self.send_prompt)

        row = QHBoxLayout()
        row.addWidget(groq_probe)
        row.addWidget(ollama_probe)
        row.addWidget(send_prompt)
        row.addStretch(1)

        box = QGroupBox("AI Controls")
        box_layout = QVBoxLayout(box)
        box_layout.addLayout(form)
        box_layout.addWidget(QLabel("Prompt Tester"))
        box_layout.addWidget(self._prompt)
        box_layout.addLayout(row)
        box_layout.addWidget(self._status)
        box_layout.addWidget(self._response)
        save_btn = QPushButton("Save AI Settings")
        save_btn.clicked.connect(self.save)
        box_layout.addWidget(save_btn)

        layout = QVBoxLayout(self)
        layout.addWidget(box)
        self._load()

    def _load(self):
        config = self.get_config()
        ai = config.get("ai", {})
        self._mode.setCurrentText(str(ai.get("mode", "auto")))
        self._groq_key.setText(str(ai.get("groq_api_key", "")))
        self._groq_model.setText(str(ai.get("groq_model", "")))
        self._ollama_host.setText(str(ai.get("ollama_host", "")))
        self._ollama_model.setText(str(ai.get("ollama_model", "")))
        self._context.setRange(256, 8192)
        self._context.setSingleStep(128)
        self._context.setValue(int(ai.get("ollama_num_ctx", 1024)))

    def save(self):
        config = self.get_config()
        ai = config.setdefault("ai", {})
        ai["mode"] = self._mode.currentText()
        ai["groq_api_key"] = self._groq_key.text().strip()
        ai["groq_model"] = self._groq_model.text().strip()
        ai["ollama_host"] = self._ollama_host.text().strip()
        ai["ollama_model"] = self._ollama_model.text().strip()
        ai["ollama_num_ctx"] = int(self._context.value())
        self.save_config(config)

    def reload_from_config(self):
        self._load()

    def send_prompt(self):
        text = self._prompt.text().strip()
        if not text:
            return
        self._response.append(f"> {text}")
        self.send_command({"command": "inject_speech", "args": {"text": text, "source": "gui"}})
        self._status.setText("Prompt injected to pet input")

    def test_groq(self):
        self._start_probe("groq", {"api_key": self._groq_key.text().strip(), "model": self._groq_model.text().strip()})

    def test_ollama(self):
        self._start_probe("ollama", {"host": self._ollama_host.text().strip()})

    def _start_probe(self, kind: str, payload: dict):
        if self._probe_thread is not None and self._probe_thread.isRunning():
            try:
                self._probe_worker.finished.disconnect(self._probe_thread.quit)
            except Exception:
                pass
            self._probe_thread.quit()
            self._probe_thread.wait(2000)
        self._status.setText("Probing...")
        self._probe_thread = QThread(self)
        self._probe_worker = ProbeWorker(kind, payload)
        self._probe_worker.moveToThread(self._probe_thread)
        self._probe_thread.started.connect(self._probe_worker.run)
        self._probe_worker.result.connect(self._on_probe_result, Qt.QueuedConnection)
        self._probe_worker.finished.connect(self._probe_thread.quit)
        self._probe_worker.finished.connect(self._probe_worker.deleteLater)
        self._probe_thread.finished.connect(self._probe_thread.deleteLater)
        self._probe_thread.finished.connect(self._clear_probe_refs)
        self._probe_thread.start()

    @Slot(object)
    def _on_probe_result(self, result):
        self._status.setText(result.get("text", "Done"))
        if result.get("ok"):
            self._response.append(json.dumps(result.get("raw", {}), indent=2)[:2000])
        else:
            QMessageBox.warning(self, "Probe Failed", result.get("text", "Unknown error"))

    @Slot()
    def _clear_probe_refs(self):
        self._probe_thread = None
        self._probe_worker = None
