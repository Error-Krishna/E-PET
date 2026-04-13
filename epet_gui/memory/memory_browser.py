from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from datetime import datetime

from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot, QTimer
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTabWidget,
    QTableView,
    QVBoxLayout,
    QWidget,
    QGroupBox,
)


class MemoryQueryWorker(QObject):
    result = Signal(str, int, object)
    finished = Signal()

    def __init__(self, db_path: Path, mode: str, job_id: int, payload: dict | None = None):
        super().__init__()
        self.db_path = Path(db_path)
        self.mode = mode
        self.job_id = job_id
        self.payload = payload or {}
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    @Slot()
    def run(self):
        conn = None
        try:
            if self._cancelled:
                return
            if not self.db_path.exists():
                self.result.emit(self.mode, self.job_id, {"error": "Memory database not found - run E-Pet first"})
                return
            conn = sqlite3.connect(str(self.db_path))
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            if self.mode == "facts":
                if self._cancelled:
                    return
                cur.execute("SELECT key, value FROM kv ORDER BY key")
                rows = [(r["key"], r["value"], "") for r in cur.fetchall()]
                self.result.emit(self.mode, self.job_id, rows)
            elif self.mode == "history":
                limit = int(self.payload.get("limit", 50))
                cur.execute("SELECT value FROM memories WHERE category='conversation' AND key='history'")
                row = cur.fetchone()
                history = []
                if row and row["value"]:
                    try:
                        history = json.loads(row["value"])
                    except Exception:
                        history = []
                self.result.emit(self.mode, self.job_id, history[-limit:])
            elif self.mode == "stats":
                stats = {}
                cur.execute("SELECT COUNT(*) FROM memories WHERE category='facts'")
                stats["facts_total"] = int(cur.fetchone()[0] or 0)
                cur.execute("SELECT COUNT(*) FROM events")
                stats["event_count"] = int(cur.fetchone()[0] or 0)
                cur.execute("SELECT COUNT(*) FROM memories")
                stats["memory_count"] = int(cur.fetchone()[0] or 0)
                cur.execute("SELECT value FROM memories WHERE category='personality' AND key='interaction_count'")
                row = cur.fetchone()
                stats["interaction_count"] = int(row[0]) if row and row[0] else 0
                cur.execute("SELECT value FROM memories WHERE category='personality' AND key='bond_level'")
                row = cur.fetchone()
                stats["bond_level"] = row[0] if row and row[0] else "0.00"
                self.result.emit(self.mode, self.job_id, stats)
            elif self.mode == "events":
                prefix = str(self.payload.get("prefix", "")).strip()
                if prefix:
                    cur.execute("SELECT id, event_type, data, timestamp FROM events WHERE event_type LIKE ? ORDER BY id DESC LIMIT 200", (f"{prefix}%",))
                else:
                    cur.execute("SELECT id, event_type, data, timestamp FROM events ORDER BY id DESC LIMIT 200")
                rows = [(r["id"], r["event_type"], r["data"], r["timestamp"]) for r in cur.fetchall()]
                self.result.emit(self.mode, self.job_id, rows)
        except Exception as exc:
            self.result.emit(self.mode, self.job_id, {"error": str(exc)})
        finally:
            try:
                if conn is not None:
                    conn.close()
            except Exception:
                pass
            self.finished.emit()


class MemoryBrowser(QWidget):
    def __init__(self, db_path: Path, parent=None):
        super().__init__(parent)
        self.db_path = Path(db_path)
        self._last_refreshed = 0.0
        self._history_limit = 50
        self._active_jobs: dict[str, tuple[int, QThread, MemoryQueryWorker]] = {}
        self._query_seq = 0

        self._fact_model = QStandardItemModel(0, 3)
        self._fact_model.setHorizontalHeaderLabels(["Key", "Value", "Updated At"])
        self._facts_table = QTableView()
        self._facts_table.setModel(self._fact_model)

        self._event_model = QStandardItemModel(0, 4)
        self._event_model.setHorizontalHeaderLabels(["ID", "Topic", "Data", "Timestamp"])
        self._events_table = QTableView()
        self._events_table.setModel(self._event_model)

        self._history_list = QListWidget()
        self._stats_box = QGroupBox("Interaction Stats")
        self._stats_layout = QVBoxLayout(self._stats_box)
        self._stats_values = {
            "facts_total": QLabel("0"),
            "event_count": QLabel("0"),
            "memory_count": QLabel("0"),
            "interaction_count": QLabel("0"),
            "bond_level": QLabel("0.00"),
        }
        for key, label in self._stats_values.items():
            row = QHBoxLayout()
            row.addWidget(QLabel(key.replace("_", " ").title()))
            row.addWidget(label)
            self._stats_layout.addLayout(row)

        self._last_refreshed_label = QLabel("Last refreshed: never")

        self._facts_search = QLineEdit()
        self._facts_search.setPlaceholderText("Search facts...")
        self._facts_search.textChanged.connect(self._filter_facts)
        self._event_prefix = QLineEdit()
        self._event_prefix.setPlaceholderText("Filter topic prefix...")
        self._event_prefix.textChanged.connect(self.refresh_events)

        facts_page = QWidget()
        facts_layout = QVBoxLayout(facts_page)
        facts_layout.addWidget(self._facts_search)
        facts_layout.addWidget(self._facts_table)

        history_page = QWidget()
        history_layout = QVBoxLayout(history_page)
        history_buttons = QHBoxLayout()
        load_more = QPushButton("Load More")
        load_more.clicked.connect(self._load_more_history)
        history_buttons.addWidget(load_more)
        history_buttons.addStretch(1)
        history_layout.addLayout(history_buttons)
        history_layout.addWidget(self._history_list)

        stats_page = QWidget()
        stats_layout = QVBoxLayout(stats_page)
        stats_layout.addWidget(self._stats_box)
        stats_layout.addWidget(self._last_refreshed_label)
        stats_layout.addStretch(1)

        events_page = QWidget()
        events_layout = QVBoxLayout(events_page)
        events_layout.addWidget(self._event_prefix)
        events_layout.addWidget(self._events_table)

        self._tabs = QTabWidget()
        self._tabs.addTab(facts_page, "Facts")
        self._tabs.addTab(history_page, "Conversation")
        self._tabs.addTab(stats_page, "Stats")
        self._tabs.addTab(events_page, "Raw Events")
        self._tabs.currentChanged.connect(self._tab_changed)

        layout = QVBoxLayout(self)
        layout.addWidget(self._tabs)

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(30000)
        self._refresh_timer.timeout.connect(self.refresh_visible_tab)
        self._refresh_timer.start()
        QTimer.singleShot(0, self.refresh_all)

    def _tab_changed(self):
        self.refresh_visible_tab()

    def refresh_visible_tab(self):
        idx = self._tabs.currentIndex()
        if idx == 0:
            self.refresh_facts()
        elif idx == 1:
            self.refresh_history()
        elif idx == 2:
            self.refresh_stats()
        elif idx == 3:
            self.refresh_events()

    def refresh_all(self):
        self.refresh_facts()
        self.refresh_history()
        self.refresh_stats()
        self.refresh_events()

    def set_db_path(self, db_path: Path):
        # Each query opens its own SQLite connection, so swapping the path is
        # safe even while earlier jobs are finishing in the GUI thread.
        self.db_path = Path(db_path)
        self.refresh_all()

    def refresh_facts(self):
        self._query("facts")

    def refresh_history(self):
        self._query("history", {"limit": self._history_limit})

    def refresh_stats(self):
        self._query("stats")

    def refresh_events(self):
        self._query("events", {"prefix": self._event_prefix.text().strip()})

    def _load_more_history(self):
        self._history_limit += 50
        self.refresh_history()

    def _filter_facts(self):
        query = self._facts_search.text().strip().lower()
        for row in range(self._fact_model.rowCount()):
            match = query in " ".join(
                self._fact_model.item(row, col).text().lower() for col in range(self._fact_model.columnCount())
            )
            self._facts_table.setRowHidden(row, not match)

    def _query(self, mode: str, payload: dict | None = None):
        self._query_seq += 1
        job_id = self._query_seq
        if mode in self._active_jobs:
            _, old_thread, old_worker = self._active_jobs[mode]
            old_worker.cancel()
            old_thread.requestInterruption()
        thread = QThread(self)
        worker = MemoryQueryWorker(self.db_path, mode, job_id, payload)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.result.connect(self._handle_result, Qt.QueuedConnection)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._active_jobs[mode] = (job_id, thread, worker)

        def _drop_job():
            # Signal delivery lands back on the GUI thread, so this cleanup does
            # not need an explicit lock even with several overlapping queries.
            current = self._active_jobs.get(mode)
            if current and current[0] == job_id:
                self._active_jobs.pop(mode, None)

        thread.finished.connect(_drop_job)
        thread.start()

    @Slot(str, int, object)
    def _handle_result(self, mode: str, job_id: int, data):
        current = self._active_jobs.get(mode)
        if current and current[0] != job_id:
            return
        if isinstance(data, dict) and data.get("error"):
            self._last_refreshed_label.setText(data["error"])
            return
        if mode == "facts":
            self._populate_facts(data)
        elif mode == "history":
            self._populate_history(data)
        elif mode == "stats":
            self._populate_stats(data)
        elif mode == "events":
            self._populate_events(data)
        self._last_refreshed = time.time()
        self._last_refreshed_label.setText("Last refreshed: just now")

    def _populate_facts(self, rows):
        self._fact_model.removeRows(0, self._fact_model.rowCount())
        for key, value, updated_at in rows:
            self._fact_model.appendRow([QStandardItem(str(key)), QStandardItem(str(value)), QStandardItem(str(updated_at))])
        self._filter_facts()

    def _populate_history(self, rows):
        self._history_list.clear()
        for msg in rows:
            role = msg.get("role", "assistant")
            text = msg.get("text", "")
            ts = msg.get("timestamp", 0)
            item = QListWidgetItem(f"{role}: {text}")
            try:
                item.setToolTip(datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M:%S"))
            except Exception:
                item.setToolTip(str(ts))
            item.setTextAlignment(Qt.AlignRight if role == "user" else Qt.AlignLeft)
            self._history_list.addItem(item)

    def _populate_stats(self, stats):
        for key, label in self._stats_values.items():
            label.setText(str(stats.get(key, 0)))

    def _populate_events(self, rows):
        self._event_model.removeRows(0, self._event_model.rowCount())
        for row in rows:
            self._event_model.appendRow([QStandardItem(str(row[0])), QStandardItem(str(row[1])), QStandardItem(str(row[2])), QStandardItem(str(row[3]))])
