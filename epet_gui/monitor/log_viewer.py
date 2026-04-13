from __future__ import annotations

import os
from collections import deque
from pathlib import Path

from PySide6.QtCore import QObject, QFileSystemWatcher, QThread, QTimer, Signal, Slot, Qt, QUrl
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtGui import QDesktopServices

from epet_gui.ipc.bridge import read_json_file, resolve_log_file_path


class LogTailWorker(QObject):
    lineReceived = Signal(str)
    statusChanged = Signal(str)

    def __init__(self, log_path: Path):
        super().__init__()
        self.log_path = Path(log_path)
        self._watcher = None
        self._timer = None
        self._fh = None
        self._position = 0
        self._inode = None

    @Slot()
    def start(self):
        self._watcher = QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(self._poll)
        self._watcher.directoryChanged.connect(self._poll)
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._poll)
        self._timer.start()
        self._open_log(initial=True)
        if self.log_path.exists():
            self._watcher.addPath(str(self.log_path))
        parent = str(self.log_path.parent)
        if parent and parent not in self._watcher.directories():
            # The directory watcher is a coarse fallback on macOS; the 500 ms
            # poll still does the authoritative tailing work.
            self._watcher.addPath(parent)
        self._poll()

    @Slot(str)
    def set_log_path(self, path: str):
        self.log_path = Path(path)
        self._open_log(initial=False)
        self._poll()

    @Slot()
    def stop(self):
        if self._timer is not None:
            self._timer.stop()
        if self._fh is not None:
            try:
                self._fh.close()
            except Exception:
                pass

    def _open_log(self, initial: bool = False):
        if not self.log_path.exists():
            self.statusChanged.emit("Log file not found - start E-Pet to begin logging")
            return
        try:
            stat = self.log_path.stat()
            self._inode = getattr(stat, "st_ino", None)
            if self._fh is not None:
                self._fh.close()
            self._fh = self.log_path.open("r", encoding="utf-8", errors="replace")
            self._position = self._fh.seek(0, os.SEEK_END if initial else os.SEEK_SET)
            self.statusChanged.emit(f"Tailing {self.log_path}")
        except Exception as exc:
            self.statusChanged.emit(f"Failed to open log file: {exc}")

    def _poll(self):
        if not self.log_path.exists():
            self.statusChanged.emit("Log file not found - start E-Pet to begin logging")
            return
        try:
            stat = self.log_path.stat()
            inode = getattr(stat, "st_ino", None)
            if self._inode is not None and self._inode != 0 and inode != self._inode:
                self._open_log(initial=False)
            if self._fh is None:
                self._open_log(initial=False)
            if self._fh is None:
                return
            self._fh.seek(self._position)
            for line in self._fh:
                self.lineReceived.emit(line.rstrip("\n"))
            self._position = self._fh.tell()
        except Exception as exc:
            self.statusChanged.emit(f"Log tail error: {exc}")


class LogViewer(QWidget):
    pathChanged = Signal(str)

    def __init__(self, log_path: Path, on_log_path_changed=None, parent=None):
        super().__init__(parent)
        self.log_path = Path(log_path)
        self.on_log_path_changed = on_log_path_changed
        self._buffer = deque(maxlen=10000)
        self._level_filter = {"DEBUG": True, "INFO": True, "WARNING": True, "ERROR": True, "CRITICAL": True}
        self._rerender_timer = QTimer(self)
        self._rerender_timer.setSingleShot(True)
        self._rerender_timer.setInterval(150)
        self._rerender_timer.timeout.connect(self._rerender)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Filter keyword or regex...")
        self._search.textChanged.connect(self._schedule_rerender)

        self._path_label = QLabel(str(self.log_path))
        self._status = QLabel("Waiting for log file...")
        self._text = QTextEdit()
        self._text.setReadOnly(True)
        self._text.setAcceptRichText(False)

        top = QHBoxLayout()
        for level in ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]:
            box = QCheckBox(level)
            box.setChecked(True)
            box.toggled.connect(lambda checked, level=level: self._set_level(level, checked))
            top.addWidget(box)
        top.addWidget(self._search, 1)
        clear_btn = QPushButton("Clear Display")
        clear_btn.clicked.connect(self._text.clear)
        top.addWidget(clear_btn)
        copy_btn = QPushButton("Copy All")
        copy_btn.clicked.connect(self._copy_all)
        top.addWidget(copy_btn)
        open_btn = QPushButton("Open Log File")
        open_btn.clicked.connect(self._open_log_file)
        top.addWidget(open_btn)
        browse_btn = QPushButton("Browse")
        browse_btn.clicked.connect(self._browse)
        top.addWidget(browse_btn)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self._path_label)
        layout.addWidget(self._status)
        layout.addWidget(self._text, 1)

        self._thread = QThread(self)
        self._worker = LogTailWorker(self.log_path)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.start)
        self.pathChanged.connect(self._worker.set_log_path, Qt.QueuedConnection)
        self._worker.lineReceived.connect(self._append_line, Qt.QueuedConnection)
        self._worker.statusChanged.connect(self._update_status, Qt.QueuedConnection)
        self._thread.start()

    def closeEvent(self, event):
        self._worker.stop()
        self._thread.quit()
        self._thread.wait(1000)
        super().closeEvent(event)

    def set_log_path(self, log_path: Path):
        self.log_path = Path(log_path)
        self._path_label.setText(str(self.log_path))
        self.pathChanged.emit(str(self.log_path))

    def _set_level(self, level: str, checked: bool):
        self._level_filter[level] = checked
        self._schedule_rerender()

    def _schedule_rerender(self):
        self._rerender_timer.start()

    def _copy_all(self):
        QApplication.clipboard().setText("\n".join(self._buffer))

    def _browse(self):
        from PySide6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getOpenFileName(self, "Select Log File", str(self.log_path.parent), "Log Files (*.log *.txt);;All Files (*)")
        if path:
            self.set_log_path(Path(path))
            if self.on_log_path_changed:
                self.on_log_path_changed(Path(path))

    def _open_log_file(self):
        if self.log_path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.log_path)))
        else:
            QMessageBox.information(self, "Log File", "Log file not found - start E-Pet to begin logging")

    @Slot(str)
    def _update_status(self, message: str):
        self._status.setText(message)

    @Slot(str)
    def _append_line(self, line: str):
        self._buffer.append(line)
        if self._passes_filter(line):
            self._insert_line(line)

    def _rerender(self):
        self._text.setUpdatesEnabled(False)
        try:
            self._text.clear()
            for line in self._buffer:
                if self._passes_filter(line):
                    self._insert_line(line)
        finally:
            self._text.setUpdatesEnabled(True)
            self._text.ensureCursorVisible()

    def _passes_filter(self, line: str) -> bool:
        if not line:
            return False
        level = _extract_level(line)
        if level and not self._level_filter.get(level, True):
            return False
        query = self._search.text().strip()
        if query:
            try:
                import re

                return re.search(query, line, re.IGNORECASE) is not None
            except re.error:
                return query.lower() in line.lower()
        return True

    def _insert_line(self, line: str):
        cursor = self._text.textCursor()
        cursor.movePosition(QTextCursor.End)
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(_line_color(line)))
        if "CRITICAL" in line:
            fmt.setFontWeight(700)
        cursor.insertText(line + "\n", fmt)
        if self._auto_scroll():
            self._text.ensureCursorVisible()

    def _auto_scroll(self) -> bool:
        bar = self._text.verticalScrollBar()
        return bar.value() >= bar.maximum() - (bar.pageStep() // 2)


def _extract_level(line: str) -> str:
    for level in ["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"]:
        if f"| {level[0]} |" in line or level in line:
            return level
    return ""


def _line_color(line: str) -> str:
    if "pet/emotion" in line:
        return "#c77dff"
    if "pet/speak" in line:
        return "#4dd0e1"
    if "pet/ai" in line:
        return "#9be564"
    if "CRITICAL" in line:
        return "#ff4d4d"
    if "ERROR" in line:
        return "#ff4444"
    if "WARNING" in line:
        return "#ffa500"
    if "DEBUG" in line:
        return "#8b949e"
    return "#f8fafc"
