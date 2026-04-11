from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from PySide6.QtCore import QTimer, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QMessageBox,
)

from core.config_validation import DEFAULT_CONFIG, normalize_and_validate_config
from epet_gui.config.schema import CONFIG_SCHEMA, PLUGIN_NAMES
from epet_gui.ipc.bridge import write_yaml_atomic


class ConfigEditor(QWidget):
    def __init__(self, config_path: Path, is_pet_running, on_saved=None, parent=None):
        super().__init__(parent)
        self.config_path = Path(config_path)
        self.is_pet_running = is_pet_running
        self.on_saved = on_saved
        self._widgets: dict[tuple[str, ...], Any] = {}
        self._plugin_checks: dict[str, QCheckBox] = {}
        self._raw_config = {}
        self._save_notice = QLabel("")
        self._save_notice.setObjectName("savedBadge")
        self._save_notice.hide()

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._body = QWidget()
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setSpacing(16)
        self._body_layout.addWidget(self._save_notice)

        self._scroll.setWidget(self._body)

        buttons = QHBoxLayout()
        save_btn = QPushButton("Save & Apply")
        save_btn.clicked.connect(self.save_config)
        revert_btn = QPushButton("Revert to Saved")
        revert_btn.clicked.connect(self.load_config)
        reset_btn = QPushButton("Reset to Defaults")
        reset_btn.clicked.connect(self.reset_to_defaults)
        raw_btn = QPushButton("Open Raw YAML")
        raw_btn.clicked.connect(self.open_raw_yaml)
        buttons.addWidget(save_btn)
        buttons.addWidget(revert_btn)
        buttons.addWidget(reset_btn)
        buttons.addWidget(raw_btn)
        buttons.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addWidget(self._scroll, 1)
        layout.addLayout(buttons)

        self.load_config()

    def load_config(self):
        if self.config_path.exists():
            with self.config_path.open("r", encoding="utf-8") as handle:
                raw = yaml.safe_load(handle) or {}
        else:
            raw = json.loads(json.dumps(DEFAULT_CONFIG))
        try:
            self._raw_config = normalize_and_validate_config(raw)
        except Exception:
            self._raw_config = json.loads(json.dumps(DEFAULT_CONFIG))
        self._build_form()
        self._apply_config_to_widgets()

    def reset_to_defaults(self):
        self._raw_config = json.loads(json.dumps(DEFAULT_CONFIG))
        self._build_form()
        self._apply_config_to_widgets()

    def open_raw_yaml(self):
        if self.config_path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.config_path)))
        else:
            QMessageBox.information(self, "Config", "config.yaml not found yet. Save once to create it.")

    def save_config(self):
        try:
            merged = json.loads(json.dumps(self._raw_config))
            for path, widget in self._widgets.items():
                self._set_path_value(merged, path, self._read_widget(widget))
            merged.setdefault("plugins", {})
            merged["plugins"]["enabled"] = [
                name for name in PLUGIN_NAMES if self._plugin_checks.get(name) and self._plugin_checks[name].isChecked()
            ]
            normalized = normalize_and_validate_config(merged)
            write_yaml_atomic(self.config_path, normalized)
            self._raw_config = normalized
            self._show_saved_notice()
            if self.on_saved:
                self.on_saved(normalized)
            if self.is_pet_running():
                QMessageBox.warning(self, "Restart Required", "Config saved. Restart the pet to apply changes.")
        except Exception as exc:
            QMessageBox.critical(self, "Save Failed", f"Could not save config.yaml:\n{exc}")

    def _show_saved_notice(self):
        self._save_notice.setText("Saved ✓")
        self._save_notice.show()
        QTimer.singleShot(2000, self._save_notice.hide)

    def _build_form(self):
        while self._body_layout.count():
            item = self._body_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._body_layout.addWidget(self._save_notice)
        self._widgets.clear()
        self._plugin_checks.clear()

        for section_name, section in CONFIG_SCHEMA.items():
            box = QGroupBox(section_name)
            if section.get("description"):
                box.setToolTip(section["description"])
            form = QVBoxLayout(box)
            if section.get("description"):
                desc = QLabel(section["description"])
                desc.setWordWrap(True)
                form.addWidget(desc)

            fields = section.get("fields", [])
            if section_name == "Plugins":
                self._build_plugin_section(form)
            else:
                form_layout = QFormLayout()
                for field in fields:
                    widget = self._build_field(field)
                    if widget is None:
                        continue
                    form_layout.addRow(field["label"], widget)
                    self._widgets[tuple(field["path"])] = widget
                form.addLayout(form_layout)
            self._body_layout.addWidget(box)
        self._body_layout.addStretch(1)

    def _build_plugin_section(self, layout):
        row = QHBoxLayout()
        for name in PLUGIN_NAMES:
            check = QCheckBox(name)
            self._plugin_checks[name] = check
            row.addWidget(check)
        row.addStretch(1)
        layout.addLayout(row)

    def _build_field(self, field):
        kind = field["type"]
        value = self._get_path_value(self._raw_config, tuple(field["path"]))
        if kind == "bool":
            widget = QCheckBox()
            widget.setChecked(bool(value if value is not None else field.get("default", False)))
            return widget
        if kind == "enum":
            widget = QComboBox()
            widget.addItems([str(choice) for choice in field.get("choices", [])])
            current = str(value if value is not None else field.get("default", ""))
            idx = widget.findText(current)
            if idx >= 0:
                widget.setCurrentIndex(idx)
            return widget
        if kind == "int":
            widget = QSpinBox()
            widget.setRange(int(field.get("min", -10**9)), int(field.get("max", 10**9)))
            widget.setSingleStep(int(field.get("step", 1)))
            widget.setValue(int(value if value is not None else field.get("default", 0)))
            return widget
        if kind == "float":
            widget = QDoubleSpinBox()
            widget.setRange(float(field.get("min", -10**9)), float(field.get("max", 10**9)))
            widget.setSingleStep(float(field.get("step", 0.1)))
            widget.setDecimals(4)
            widget.setValue(float(value if value is not None else field.get("default", 0.0)))
            return widget
        if kind in {"str", "path", "secret"}:
            container = QWidget()
            row = QHBoxLayout(container)
            row.setContentsMargins(0, 0, 0, 0)
            line = QLineEdit()
            line.setText(str(value if value is not None else field.get("default", "")))
            if kind == "secret":
                line.setEchoMode(QLineEdit.Password)
                toggle = QToolButton()
                toggle.setText("Show")

                def toggle_secret():
                    if line.echoMode() == QLineEdit.Password:
                        line.setEchoMode(QLineEdit.Normal)
                        toggle.setText("Hide")
                    else:
                        line.setEchoMode(QLineEdit.Password)
                        toggle.setText("Show")

                toggle.clicked.connect(toggle_secret)
                row.addWidget(line, 1)
                row.addWidget(toggle)
            elif kind == "path":
                browse = QPushButton("Browse")

                def browse_path():
                    path, _ = QFileDialog.getOpenFileName(self, field["label"], line.text() or str(Path.home()), "All Files (*)")
                    if path:
                        line.setText(path)

                browse.clicked.connect(browse_path)
                row.addWidget(line, 1)
                row.addWidget(browse)
            else:
                row.addWidget(line, 1)
            container._line = line  # type: ignore[attr-defined]
            return container
        if kind == "plugin_list":
            return None
        return None

    def _apply_config_to_widgets(self):
        for path, widget in self._widgets.items():
            value = self._get_path_value(self._raw_config, path)
            self._set_widget(widget, value)
        plugins = set(self._get_path_value(self._raw_config, ("plugins", "enabled")) or [])
        for name, check in self._plugin_checks.items():
            check.setChecked(name in plugins)

    def _read_widget(self, widget):
        if isinstance(widget, QCheckBox):
            return widget.isChecked()
        if isinstance(widget, QComboBox):
            return widget.currentText()
        if isinstance(widget, QSpinBox):
            return int(widget.value())
        if isinstance(widget, QDoubleSpinBox):
            return float(widget.value())
        if isinstance(widget, QWidget) and hasattr(widget, "_line"):
            return widget._line.text()  # type: ignore[attr-defined]
        return None

    def _set_widget(self, widget, value):
        if isinstance(widget, QCheckBox):
            widget.setChecked(bool(value))
        elif isinstance(widget, QComboBox):
            idx = widget.findText(str(value))
            if idx >= 0:
                widget.setCurrentIndex(idx)
        elif isinstance(widget, QSpinBox):
            widget.setValue(int(value or 0))
        elif isinstance(widget, QDoubleSpinBox):
            widget.setValue(float(value or 0.0))
        elif isinstance(widget, QWidget) and hasattr(widget, "_line"):
            widget._line.setText("" if value is None else str(value))  # type: ignore[attr-defined]

    def _get_path_value(self, data: dict, path: tuple[str, ...]):
        current = data
        for key in path:
            if not isinstance(current, dict):
                return None
            current = current.get(key)
        return current

    def _set_path_value(self, data: dict, path: tuple[str, ...], value):
        current = data
        for key in path[:-1]:
            current = current.setdefault(key, {})
        current[path[-1]] = value
