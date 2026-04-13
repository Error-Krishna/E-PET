from __future__ import annotations

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QDoubleSpinBox,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


class VoiceControls(QWidget):
    def __init__(self, get_config, save_config, send_command, parent=None):
        super().__init__(parent)
        self.get_config = get_config
        self.save_config = save_config
        self.send_command = send_command

        self._wake_word = QLineEdit()
        self._wake_enabled = QCheckBox("Enable Wake Detection")
        self._wake_status = QLabel("unknown")
        self._stt_backend = QComboBox()
        self._tts_backend = QComboBox()
        self._wake_mode = QComboBox()
        self._follow_up = QSpinBox()
        self._mic_lock_timeout = QDoubleSpinBox()
        self._last_transcript = QLabel("-")
        self._test_input = QLineEdit()

        self._stt_backend.addItems(["auto", "whisper", "faster-whisper"])
        self._tts_backend.addItems(["piper", "pyttsx3", "none"])
        self._wake_mode.addItems(["auto", "whisper", "porcupine", "keyboard"])
        self._follow_up.setRange(1, 30)
        self._mic_lock_timeout.setRange(0.5, 60.0)
        self._mic_lock_timeout.setSingleStep(0.5)
        self._mic_lock_timeout.setDecimals(1)

        form = QFormLayout()
        # Quick-access subset: the full Config editor exposes the advanced
        # voice timing and wake tuning fields.
        form.addRow("Wake Word", self._wake_word)
        form.addRow("Wake Enabled", self._wake_enabled)
        form.addRow("Wake Mode", self._wake_mode)
        form.addRow("Wake Status", self._wake_status)
        form.addRow("STT Backend", self._stt_backend)
        form.addRow("TTS Backend", self._tts_backend)
        form.addRow("Follow-up Seconds", self._follow_up)
        form.addRow("Mic Lock Timeout", self._mic_lock_timeout)

        test_row = QHBoxLayout()
        test_btn = QPushButton("Speak")
        test_btn.clicked.connect(self._speak_test)
        listen_btn = QPushButton("Test STT")
        listen_btn.clicked.connect(self._test_stt)
        test_row.addWidget(self._test_input, 1)
        test_row.addWidget(test_btn)
        test_row.addWidget(listen_btn)

        box = QGroupBox("Voice Controls")
        box_layout = QVBoxLayout(box)
        box_layout.addLayout(form)
        box_layout.addWidget(QLabel("Last transcription"))
        box_layout.addWidget(self._last_transcript)
        box_layout.addLayout(test_row)
        save_btn = QPushButton("Save Voice Settings")
        save_btn.clicked.connect(self.save)
        box_layout.addWidget(save_btn)

        layout = QVBoxLayout(self)
        layout.addWidget(box)

        self._load()

    def _load(self):
        config = self.get_config()
        voice = config.get("voice", {})
        self._wake_word.setText(str(voice.get("wake_word", "")))
        self._wake_enabled.setChecked(bool(voice.get("enabled", True)))
        self._wake_mode.setCurrentText(str(voice.get("wake_mode", "auto")))
        self._wake_status.setText("Listening" if self._wake_enabled.isChecked() else "Inactive")
        self._set_combo(self._stt_backend, str(voice.get("stt_backend", "auto")))
        self._set_combo(self._tts_backend, str(voice.get("tts_backend", "piper")))
        # Older configs used wake_listen_seconds before follow_up_listen_seconds
        # was added, so keep the fallback for backward compatibility.
        self._follow_up.setValue(int(voice.get("follow_up_listen_seconds", voice.get("wake_listen_seconds", 2))))
        self._mic_lock_timeout.setValue(float(voice.get("mic_lock_timeout", 5.0)))

    def _set_combo(self, combo: QComboBox, value: str):
        idx = combo.findText(value)
        if idx >= 0:
            combo.setCurrentIndex(idx)

    def save(self):
        config = self.get_config()
        config.setdefault("voice", {})
        config["voice"]["wake_word"] = self._wake_word.text().strip() or "hey pip"
        config["voice"]["enabled"] = self._wake_enabled.isChecked()
        config["voice"]["wake_mode"] = self._wake_mode.currentText()
        config["voice"]["stt_backend"] = self._stt_backend.currentText()
        config["voice"]["tts_backend"] = self._tts_backend.currentText()
        config["voice"]["follow_up_listen_seconds"] = int(self._follow_up.value())
        config["voice"]["mic_lock_timeout"] = float(self._mic_lock_timeout.value())
        self.save_config(config)

    def reload_from_config(self):
        self._load()

    def _speak_test(self):
        text = self._test_input.text().strip() or "Hello from the control center!"
        self.send_command({"command": "speak", "args": {"text": text}})

    def _test_stt(self):
        QMessageBox.information(self, "STT Test", "Use the live pet process to verify STT. Phase 2 IPC can stream transcriptions back here.")
        self.send_command({"command": "inject_speech", "args": {"text": self._test_input.text().strip() or "Testing voice input from the control center."}})
