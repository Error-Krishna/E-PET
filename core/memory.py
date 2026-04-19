from __future__ import annotations

import json
import logging
import re
import shutil
import sqlite3
import tempfile
import threading
import time
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_MEMORY_FILENAME = "epet.kv.json"
LEGACY_MEMORY_FILENAMES = ("epet.db",)


class Memory:
    """Persistent KV memory with categorized memories and an event log.

    The backend is a JSON file on disk, so the pet can keep lightweight memory
    without depending on a SQL database. Existing SQLite memories are migrated
    automatically the first time they are opened.
    """

    backend = "kv"

    def __init__(self, db_path: str = DEFAULT_MEMORY_FILENAME):
        self._closed = False
        self._path = None if db_path == ":memory:" else Path(db_path).expanduser()
        self._lock = threading.RLock()
        self._state = self._default_state()
        self._dirty = False
        self._write_count = 0

        if self._path is None:
            return

        self._path.parent.mkdir(parents=True, exist_ok=True)
        if self._path.exists():
            self._state = self._load_existing_state(self._path)
        elif self._legacy_path_exists(self._path):
            legacy_path = self._legacy_path(self._path)
            logger.info("Memory: using legacy store at %s for %s", legacy_path, self._path)
            self._state = self._load_existing_state(legacy_path)
            self._dirty = True
            self._flush_locked()
        else:
            self._dirty = True
            self._flush_locked()

    def _ensure_open(self):
        if self._closed:
            raise RuntimeError("Memory is closed")

    def _default_state(self) -> dict[str, Any]:
        return {
            "version": 1,
            "kv": {},
            "memories": {},
            "events": [],
            "meta": {
                "created_at": time.time(),
                "next_event_id": 1,
            },
        }

    def _normalize_state(self, raw: Any) -> dict[str, Any]:
        state = self._default_state()
        if not isinstance(raw, dict):
            return state

        kv = raw.get("kv", {})
        memories = raw.get("memories", {})
        events = raw.get("events", [])
        meta = raw.get("meta", {})

        if isinstance(kv, dict):
            state["kv"] = {str(key): str(value) for key, value in kv.items()}

        if isinstance(memories, dict):
            normalized_memories: dict[str, dict[str, str]] = {}
            for category, items in memories.items():
                if not isinstance(items, dict):
                    continue
                normalized_memories[str(category)] = {
                    str(key): str(value) for key, value in items.items()
                }
            state["memories"] = normalized_memories

        if isinstance(events, list):
            normalized_events = []
            next_event_id = 1
            for item in events:
                if not isinstance(item, dict):
                    continue
                event_id = int(item.get("id") or next_event_id)
                next_event_id = max(next_event_id, event_id + 1)
                normalized_events.append(
                    {
                        "id": event_id,
                        "event_type": str(item.get("event_type", "")),
                        "data": str(item.get("data", "")),
                        "timestamp": str(item.get("timestamp", "")),
                    }
                )
            state["events"] = normalized_events
            state["meta"]["next_event_id"] = next_event_id

        if isinstance(meta, dict):
            state["meta"]["created_at"] = float(meta.get("created_at", state["meta"]["created_at"]))
            state["meta"]["next_event_id"] = max(
                int(meta.get("next_event_id", state["meta"]["next_event_id"])),
                int(state["meta"]["next_event_id"]),
            )

        return state

    def _looks_like_sqlite(self, path: Path) -> bool:
        try:
            with path.open("rb") as handle:
                return handle.read(16).startswith(b"SQLite format 3\x00")
        except Exception:
            return False

    def _legacy_path(self, path: Path) -> Path | None:
        if path is None:
            return None
        for legacy_name in LEGACY_MEMORY_FILENAMES:
            candidate = path.with_name(legacy_name)
            if candidate.exists():
                return candidate
        return None

    def _legacy_path_exists(self, path: Path) -> bool:
        return self._legacy_path(path) is not None

    def _load_existing_state(self, path: Path) -> dict[str, Any]:
        if self._looks_like_sqlite(path):
            logger.info("Memory: migrating legacy SQLite store at %s to KV JSON", path)
            state = self._migrate_from_sqlite(path)
            self._dirty = True
            self._flush_locked()
            return state

        try:
            with path.open("r", encoding="utf-8") as handle:
                content = handle.read()
            if not content.strip():
                logger.info("Memory: %s is empty, starting fresh", path)
                return self._default_state()
            raw = json.loads(content)
            return self._normalize_state(raw)
        except Exception as exc:
            logger.warning("Memory: failed to load %s, starting fresh (%s)", path, exc)
            return self._default_state()

    def _migrate_from_sqlite(self, path: Path) -> dict[str, Any]:
        state = self._default_state()
        backup_path = path.with_name(f"{path.name}.sqlite.bak")
        try:
            shutil.copy2(path, backup_path)
        except Exception as exc:
            logger.warning("Memory: unable to create SQLite backup at %s (%s)", backup_path, exc)

        conn = sqlite3.connect(str(path))
        try:
            cursor = conn.cursor()
            try:
                cursor.execute("SELECT key, value FROM kv")
                for key, value in cursor.fetchall():
                    state["kv"][str(key)] = "" if value is None else str(value)
            except Exception:
                pass

            try:
                cursor.execute("SELECT category, key, value FROM memories")
                for category, key, value in cursor.fetchall():
                    cat = state["memories"].setdefault(str(category), {})
                    cat[str(key)] = "" if value is None else str(value)
            except Exception:
                pass

            try:
                cursor.execute("SELECT id, event_type, data, timestamp FROM events ORDER BY id ASC")
                next_event_id = 1
                events = []
                for event_id, event_type, data, timestamp in cursor.fetchall():
                    event_id = int(event_id or next_event_id)
                    next_event_id = max(next_event_id, event_id + 1)
                    events.append(
                        {
                            "id": event_id,
                            "event_type": str(event_type or ""),
                            "data": "" if data is None else str(data),
                            "timestamp": str(timestamp or ""),
                        }
                    )
                state["events"] = events
                state["meta"]["next_event_id"] = next_event_id
            except Exception:
                pass
        finally:
            conn.close()
        return self._normalize_state(state)

    def _serialize_state(self) -> str:
        payload = deepcopy(self._state)
        payload["version"] = 1
        payload["meta"]["updated_at"] = time.time()
        return json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=False) + "\n"

    def _flush_locked(self):
        if self._path is None:
            self._dirty = False
            return
        tmp_fd = None
        tmp_path = None
        try:
            tmp_fd, tmp_path = tempfile.mkstemp(
                prefix=f"{self._path.name}.",
                suffix=".tmp",
                dir=str(self._path.parent),
            )
            with open(tmp_fd, "w", encoding="utf-8") as handle:
                handle.write(self._serialize_state())
            Path(tmp_path).replace(self._path)
            self._dirty = False
            self._write_count += 1
        except Exception:
            raise
        finally:
            if tmp_path is not None:
                try:
                    Path(tmp_path).unlink(missing_ok=True)
                except Exception:
                    pass

    def _flush(self):
        with self._lock:
            self._flush_locked()

    def _stringify(self, value: Any) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, (dict, list, tuple)):
            return json.dumps(value, ensure_ascii=False)
        return str(value)

    def set(self, key: str, value: str) -> None:
        self._ensure_open()
        with self._lock:
            self._state["kv"][str(key)] = self._stringify(value)
            self._dirty = True
            self._flush_locked()
        logger.debug("Memory set: %s = %s", key, value)

    def get(self, key: str) -> str | None:
        self._ensure_open()
        with self._lock:
            return self._state["kv"].get(str(key))

    def remember(self, category: str, key: str, value: str) -> None:
        self._ensure_open()
        with self._lock:
            bucket = self._state["memories"].setdefault(str(category), {})
            bucket[str(key)] = self._stringify(value)
            self._dirty = True
            self._flush_locked()
        logger.debug("Remembered: %s:%s = %s", category, key, value)

    def recall(self, category: str, key: str) -> str | None:
        self._ensure_open()
        with self._lock:
            return self._state["memories"].get(str(category), {}).get(str(key))

    def log_event(self, event_type: str, data: str) -> None:
        self._ensure_open()
        with self._lock:
            event_id = int(self._state["meta"].get("next_event_id", 1))
            self._state["events"].append(
                {
                    "id": event_id,
                    "event_type": str(event_type),
                    "data": self._stringify(data),
                    "timestamp": datetime.utcnow().isoformat(timespec="seconds"),
                }
            )
            self._state["meta"]["next_event_id"] = event_id + 1
            self._dirty = True
            self._flush_locked()
        logger.debug("Event logged: %s -> %s", event_type, data)

    def prune_events(self, keep_last: int = 10000) -> None:
        self._ensure_open()
        keep_last = max(0, int(keep_last))
        with self._lock:
            if keep_last <= 0:
                self._state["events"] = []
            elif len(self._state["events"]) > keep_last:
                self._state["events"] = self._state["events"][-keep_last:]
            self._dirty = True
            self._flush_locked()

    def list_kv(self, prefix: str = "") -> list[tuple[str, str]]:
        self._ensure_open()
        prefix = str(prefix or "")
        with self._lock:
            items = [
                (key, value)
                for key, value in self._state["kv"].items()
                if key.startswith(prefix)
            ]
        return sorted(items, key=lambda item: item[0])

    def list_memories(self, category: str) -> list[tuple[str, str]]:
        self._ensure_open()
        with self._lock:
            items = list(self._state["memories"].get(str(category), {}).items())
        return sorted(items, key=lambda item: item[0])

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        self._ensure_open()
        needle = str(query or "").strip().lower()
        limit = max(0, int(limit))
        if not needle or limit == 0:
            return []

        terms = [term for term in re.split(r"\s+", needle) if term]
        results: list[dict[str, Any]] = []

        def score_text(text: str) -> int:
            haystack = str(text or "").lower()
            if not haystack:
                return 0
            score = 0
            for term in terms:
                if term in haystack:
                    score += 2
            if needle in haystack:
                score += 4
            return score

        with self._lock:
            for key, value in self._state["kv"].items():
                score = score_text(f"{key} {value}")
                if score:
                    results.append(
                        {
                            "source": "kv",
                            "key": key,
                            "value": value,
                            "score": score,
                        }
                    )

            for category, items in self._state["memories"].items():
                for key, value in items.items():
                    score = score_text(f"{category} {key} {value}")
                    if score:
                        results.append(
                            {
                                "source": "memory",
                                "category": category,
                                "key": key,
                                "value": value,
                                "score": score,
                            }
                        )

            for event in self._state["events"]:
                blob = " ".join(
                    str(event.get(field, ""))
                    for field in ("event_type", "data", "timestamp")
                )
                score = score_text(blob)
                if score:
                    results.append(
                        {
                            "source": "event",
                            "event_type": str(event.get("event_type", "")),
                            "data": str(event.get("data", "")),
                            "timestamp": str(event.get("timestamp", "")),
                            "score": score,
                        }
                    )

        results.sort(key=lambda item: (-int(item.get("score", 0)), item.get("source", "")))
        return results[:limit]

    def get_conversation_history(self, limit: int | None = None) -> list[dict[str, Any]]:
        self._ensure_open()
        raw = self.recall("conversation", "history")
        if not raw:
            return []
        try:
            history = json.loads(raw)
        except Exception:
            return []
        if not isinstance(history, list):
            return []
        if limit is None:
            return [item for item in history if isinstance(item, dict)]
        limit = max(0, int(limit))
        if limit == 0:
            return []
        return [item for item in history[-limit:] if isinstance(item, dict)]

    def get_events(self, prefix: str = "", limit: int = 200) -> list[dict[str, Any]]:
        self._ensure_open()
        prefix = str(prefix or "")
        limit = max(0, int(limit))
        with self._lock:
            events = [
                dict(event)
                for event in self._state["events"]
                if not prefix or str(event.get("event_type", "")).startswith(prefix)
            ]
        if limit:
            events = events[-limit:]
        return events

    def stats(self) -> dict[str, Any]:
        self._ensure_open()
        with self._lock:
            facts_total = len(self._state["memories"].get("facts", {}))
            conversation_count = len(self.get_conversation_history())
            event_count = len(self._state["events"])
            memory_count = sum(len(items) for items in self._state["memories"].values()) + len(self._state["kv"])
            interaction_count = self._state["memories"].get("personality", {}).get("interaction_count", "0")
            current_mood = self._state["kv"].get("current_mood", "")
        try:
            interaction_count_value = int(str(interaction_count or "0"))
        except Exception:
            interaction_count_value = 0
        stats = {
            "facts_total": facts_total,
            "conversation_count": conversation_count,
            "event_count": event_count,
            "memory_count": memory_count,
            "interaction_count": interaction_count_value,
        }
        if current_mood:
            stats["current_mood"] = current_mood
        return stats

    def export(self) -> dict[str, Any]:
        self._ensure_open()
        with self._lock:
            return deepcopy(self._state)

    def close(self):
        if self._closed:
            return
        self._closed = True
        with self._lock:
            try:
                if self._dirty:
                    self._flush_locked()
            except Exception:
                pass
