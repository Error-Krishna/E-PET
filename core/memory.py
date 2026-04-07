import sqlite3
import logging
import threading
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)

class Memory:
    """SQLite-backed memory system with key-value store, event log, and categorized memories."""
    def __init__(self, db_path: str = "epet.db"):
        if db_path != ":memory:":
            path = Path(db_path).expanduser()
            path.parent.mkdir(parents=True, exist_ok=True)
            db_path = str(path)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        with self._lock:
            cursor = self.conn.cursor()
            # Key-value store
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS kv (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)
            # Event log
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    data TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            # Categorized memories
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS memories (
                    category TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT,
                    PRIMARY KEY (category, key)
                )
            """)
            self.conn.commit()
        logger.debug("Memory database initialized")

    def set(self, key: str, value: str) -> None:
        """Store a key-value pair."""
        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute("INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)", (key, value))
            self.conn.commit()
        logger.debug(f"Memory set: {key} = {value}")

    def get(self, key: str) -> str | None:
        """Retrieve a value by key, or None if not found."""
        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute("SELECT value FROM kv WHERE key = ?", (key,))
            row = cursor.fetchone()
        return row[0] if row else None

    def log_event(self, event_type: str, data: str) -> None:
        """Log an event with type and data."""
        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute("INSERT INTO events (event_type, data) VALUES (?, ?)", (event_type, data))
            self.conn.commit()
        logger.debug(f"Event logged: {event_type} -> {data}")

    def remember(self, category: str, key: str, value: str) -> None:
        """Store a memory under a category and key."""
        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute(
                "INSERT OR REPLACE INTO memories (category, key, value) VALUES (?, ?, ?)",
                (category, key, value)
            )
            self.conn.commit()
        logger.debug(f"Remembered: {category}:{key} = {value}")

    def recall(self, category: str, key: str) -> str | None:
        """Retrieve a memory by category and key, or None if not found."""
        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute(
                "SELECT value FROM memories WHERE category = ? AND key = ?",
                (category, key)
            )
            row = cursor.fetchone()
        return row[0] if row else None

    def close(self):
        with self._lock:
            self.conn.close()
