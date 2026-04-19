from __future__ import annotations

import json
import time
from copy import deepcopy
from pathlib import Path

import yaml
from PySide6.QtCore import QSettings, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QStatusBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import QProcess

from core.config_validation import DEFAULT_CONFIG, normalize_and_validate_config
from core.platform_utils import get_project_root
from epet_gui.config.config_editor import ConfigEditor
from epet_gui.control.ai_controls import AIControls
from epet_gui.control.pet_controls import PetControls
from epet_gui.control.voice_controls import VoiceControls
from epet_gui.ipc.bridge import (
    command_file_path,
    dispatch_command,
    project_root,
    resolve_log_file_path,
    state_file_path,
    write_yaml_atomic,
)
from epet_gui.memory.memory_browser import MemoryBrowser
from epet_gui.monitor.log_viewer import LogViewer
from epet_gui.monitor.pet_state import PetStatePanel
from epet_gui.monitor.status_bar import PetStatusBarWidget


class EpetControlCenter(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("E-Pet Control Center")
        self.settings = QSettings("EPet", "E-Pet Control Center")
        self.root = project_root()
        self.config_path = self._discover_config_path()
        self.config = self._load_config()
        self.log_path = resolve_log_file_path(self.config, self.config_path)
        self.db_path = self._resolve_db_path()

        self._sidebar_buttons: list[QToolButton] = []
        self._last_state = {}
        self._runtime_status = "Stopped"

        self._central = QWidget()
        self.setCentralWidget(self._central)
        self._outer = QHBoxLayout(self._central)
        self._outer.setContentsMargins(0, 0, 0, 0)
        self._outer.setSpacing(0)

        self._sidebar = self._build_sidebar()
        self._stack = QStackedWidget()
        self._outer.addWidget(self._sidebar)
        self._outer.addWidget(self._stack, 1)

        self.status_widget = PetStatusBarWidget()
        status_bar = QStatusBar()
        status_bar.addPermanentWidget(self.status_widget, 1)
        self.setStatusBar(status_bar)

        self._build_panels()
        self._restore_window_state()
        self._apply_stylesheet()

    def _build_sidebar(self):
        frame = QFrame()
        frame.setObjectName("Sidebar")
        frame.setFixedWidth(220)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        title = QLabel("E-Pet Control Center")
        title.setObjectName("SidebarTitle")
        layout.addWidget(title)
        layout.addSpacing(8)

        self._button_group = QButtonGroup(self)
        self._button_group.setExclusive(True)
        for index, name in enumerate(["Dashboard", "Config", "Logs", "Memory", "Voice", "AI", "Controls"]):
            button = QToolButton()
            button.setText(name)
            button.setToolButtonStyle(Qt.ToolButtonTextOnly)
            button.setCheckable(True)
            button.clicked.connect(lambda checked=False, idx=index: self._stack.setCurrentIndex(idx))
            self._button_group.addButton(button, index)
            self._sidebar_buttons.append(button)
            layout.addWidget(button)
        layout.addStretch(1)

        footer = QPushButton("Open Config")
        footer.clicked.connect(self._open_config_file)
        layout.addWidget(footer)
        return frame

    def _build_panels(self):
        self.dashboard = PetStatePanel(state_file_path(self.root), on_command=self.send_command)
        self.dashboard.stateUpdated.connect(self._on_state_updated)

        self.config_editor = ConfigEditor(self.config_path, is_pet_running=self.is_pet_running, on_saved=self._on_config_saved)
        self.log_viewer = LogViewer(self.log_path, on_log_path_changed=self._on_log_path_changed)
        self.memory_browser = MemoryBrowser(self.db_path)
        self.voice_controls = VoiceControls(self.get_config, self.save_config, self.send_command)
        self.ai_controls = AIControls(self.get_config, self.save_config, self.send_command)
        self.pet_controls = PetControls(self.root, self.get_config, self.save_config, self.is_pet_running, self.send_command)

        for widget in [
            self.dashboard,
            self.config_editor,
            self.log_viewer,
            self.memory_browser,
            self.voice_controls,
            self.ai_controls,
            self.pet_controls,
        ]:
            self._stack.addWidget(widget)

        last_index = int(self.settings.value("ui/last_tab", 0))
        self._set_tab(last_index)

    def _set_tab(self, index: int):
        index = max(0, min(index, self._stack.count() - 1))
        self._stack.setCurrentIndex(index)
        if index < len(self._sidebar_buttons):
            self._sidebar_buttons[index].setChecked(True)

    def _apply_stylesheet(self):
        style_path = self.root / "epet_gui" / "assets" / "style.qss"
        if style_path.exists():
            with style_path.open("r", encoding="utf-8") as handle:
                QApplication.instance().setStyleSheet(handle.read())

    def _discover_config_path(self) -> Path:
        last = self.settings.value("paths/config_path", "")
        if last:
            candidate = Path(str(last)).expanduser()
            if candidate.exists():
                return candidate
        candidates = [
            self.root / "config.yaml",
            Path(__file__).resolve().parent.parent / "config.yaml",
        ]
        for candidate in candidates:
            if candidate.exists():
                self.settings.setValue("paths/config_path", str(candidate))
                return candidate
        reply = QMessageBox.question(self, "Config Missing", "config.yaml was not found. Create a new one from defaults?")
        if reply == QMessageBox.Yes:
            candidate = self.root / "config.yaml"
            write_yaml_atomic(candidate, DEFAULT_CONFIG)
            self.settings.setValue("paths/config_path", str(candidate))
            return candidate
        path, _ = QFileDialog.getOpenFileName(self, "Select config.yaml", str(self.root), "YAML Files (*.yaml *.yml);;All Files (*)")
        if path:
            candidate = Path(path)
            self.settings.setValue("paths/config_path", str(candidate))
            return candidate
        return self.root / "config.yaml"

    def _load_config(self):
        if not self.config_path.exists():
            return deepcopy(DEFAULT_CONFIG)
        with self.config_path.open("r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle) or {}
        return normalize_and_validate_config(raw)

    def _resolve_db_path(self) -> Path:
        raw = self.config.get("memory", {}).get("db_path")
        if raw:
            path = Path(str(raw)).expanduser()
            if not path.is_absolute():
                path = self.config_path.parent / path
            if not path.exists():
                legacy_path = path.with_name("epet.db")
                if legacy_path.exists():
                    return legacy_path
            return path
        return self.root / "epet.kv.json"

    def get_config(self):
        return deepcopy(self.config)

    def save_config(self, new_config):
        normalized = normalize_and_validate_config(new_config)
        write_yaml_atomic(self.config_path, normalized)
        self._apply_config(normalized)
        self.statusBar().showMessage("Config saved", 2000)

    def _on_config_saved(self, config):
        self._apply_config(config)
        self.statusBar().showMessage("Config saved", 2000)

    def _apply_config(self, config):
        self.config = config
        self.log_path = resolve_log_file_path(self.config, self.config_path)
        self.db_path = self._resolve_db_path()
        self.log_viewer.set_log_path(self.log_path)
        self.memory_browser.set_db_path(self.db_path)
        self.voice_controls.reload_from_config()
        self.ai_controls.reload_from_config()
        self.pet_controls._sync_plugin_checks()

    def _open_config_file(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.config_path)))

    def _on_log_path_changed(self, new_path: Path):
        self.settings.setValue("paths/log_path", str(new_path))
        self.statusBar().showMessage(f"Log path set to {new_path}", 2000)

    def is_pet_running(self) -> bool:
        if self.pet_controls._process.state() != QProcess.NotRunning:
            return True
        state_path = state_file_path(self.root)
        try:
            if not state_path.exists():
                return False
            with state_path.open("r", encoding="utf-8") as handle:
                state = json.load(handle)
            if not isinstance(state, dict):
                return False
            if not state.get("running"):
                return False
            timestamp = float(state.get("timestamp", 0.0) or 0.0)
            return time.time() - timestamp < 10
        except Exception:
            return False

    def send_command(self, payload):
        from epet_gui.ipc.bridge import write_json_atomic

        write_json_atomic(command_file_path(self.root), payload)
        self.statusBar().showMessage(f"Queued command: {payload.get('command', 'unknown')}", 2000)

    def _on_state_updated(self, state: dict):
        self._last_state = dict(state or {})
        running = bool(self._last_state.get("running"))
        self._runtime_status = "Running" if running else "Stopped"
        self.status_widget.set_running(running)
        self.status_widget.set_ai_mode(str(self._last_state.get("ai_mode", "auto")))
        self.status_widget.set_last_event(str(self._last_state.get("last_event", "")))
        self.status_widget.set_uptime(int(self._last_state.get("uptime_seconds", 0)))
        if state:
            self.statusBar().showMessage(
                f"Mood: {state.get('mood', 'neutral')} | Voice: {state.get('voice_state', 'idle')}",
                1500,
            )

    def closeEvent(self, event):
        self.settings.setValue("ui/geometry", self.saveGeometry())
        self.settings.setValue("ui/last_tab", self._stack.currentIndex())
        self.settings.setValue("paths/config_path", str(self.config_path))
        self.settings.setValue("paths/log_path", str(self.log_path))
        super().closeEvent(event)

    def _restore_window_state(self):
        geometry = self.settings.value("ui/geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        else:
            self.resize(1440, 920)
            self.move(100, 60)
