from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from core.config_validation import normalize_and_validate_config
from core.event_bus import EventBus
from core.hal import HALSimulator
from core.memory import Memory
from core.platform_utils import get_config_path, get_database_path
from core.plugin_loader import PluginLoader
from simulator.face_renderer import SimpleRenderer as FaceRenderer
from simulator.input_sim import InputSimulator

logger = logging.getLogger(__name__)


def load_config(config_path: Path | None = None) -> dict[str, Any]:
    path = config_path or get_config_path()
    with path.open("r", encoding="utf-8") as handle:
        return normalize_and_validate_config(yaml.safe_load(handle))


@dataclass
class StreamlitBackend:
    """Background backend runtime for Streamlit and other headless UIs."""

    config: dict[str, Any]
    headless: bool = True

    def __post_init__(self):
        self.ready = threading.Event()
        self.stop_event = threading.Event()
        self._started = False
        self._startup_error: Exception | None = None
        self._cleanup_done = False
        self._event_queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=256)
        self._event_lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, daemon=True)

        self.bus: EventBus | None = None
        self.hal: HALSimulator | None = None
        self.memory: Memory | None = None
        self.face_renderer: FaceRenderer | None = None
        self.input_sim: InputSimulator | None = None

        self._gui_topics = {
            "pet/emotion/changed",
            "pet/sound/play",
            "pet/ai/response",
            "pet/ai/backend",
            "pet/voice/transcript",
            "pet/voice/tts_state",
            "pet/input/wake_word",
            "pet/system/tick",
        }
        self._last_ai_backend: str | None = None
        self._last_ai_backend_reason: str | None = None

    def start(self):
        if not self._started:
            self._thread.start()
            self._started = True
        if not self.ready.wait(timeout=20):
            raise TimeoutError("E-Pet backend did not become ready in time")
        if self._startup_error is not None:
            raise self._startup_error
        return self

    def stop(self):
        self.stop_event.set()
        if self._started and self._thread.is_alive():
            self._thread.join(timeout=2)
        self._cleanup()

    def publish(self, topic: str, data: Any):
        if self.bus is not None:
            self.bus.publish(topic, data)

    def drain_events(self) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        while True:
            try:
                events.append(self._event_queue.get_nowait())
            except queue.Empty:
                break
        return events

    def snapshot(self) -> dict[str, Any]:
        mood = "neutral"
        bond_level = "0.00"
        interaction_count = "0"
        facts: dict[str, str] = {}
        hal_state = {}

        if self.memory is not None:
            mood = self.memory.get("current_mood") or mood
            bond_level = self.memory.recall("personality", "bond_level") or bond_level
            interaction_count = self.memory.recall("personality", "interaction_count") or interaction_count
            name = self.memory.recall("facts", "name")
            likes = self.memory.recall("facts", "likes")
            if name:
                facts["name"] = name
            if likes:
                facts["likes"] = likes

        if self.hal is not None:
            hal_state = self.hal.get_state()
            mood = mood or hal_state.get("face", "neutral")

        return {
            "mood": mood,
            "bond_level": bond_level,
            "interaction_count": interaction_count,
            "facts": facts,
            "hal_state": hal_state,
            "ai_backend": self._last_ai_backend or "unknown",
            "ai_backend_reason": self._last_ai_backend_reason or "",
        }

    def _run(self):
        try:
            logger.info("Backend: starting")
            self.bus = EventBus()
            self.hal = HALSimulator(debug=self.config.get("hardware", {}).get("debug", False))
            self.memory = Memory(get_database_path())
            self._register_event_bridge()

            enabled_plugins = self.config.get("plugins", {}).get("enabled", [])
            loader = PluginLoader(enabled_plugins, self.bus, self.hal, self.memory, self.config)
            loader.load_plugins()

            if not self.headless:
                self.face_renderer = FaceRenderer(self.bus, self.hal, self.memory, self.config)
                self.face_renderer.start()
                self.input_sim = InputSimulator(self.bus)
                self.input_sim.start()

            self.bus.publish("pet/sound/play", {"name": "startup"})
            logger.info("Backend: ready")
        except Exception as exc:
            self._startup_error = exc
            logger.exception("Backend startup failed")
        finally:
            self.ready.set()

        self.stop_event.wait()
        self._cleanup()

    def _register_event_bridge(self):
        assert self.bus is not None
        for topic in self._gui_topics:
            self.bus.subscribe(topic, self._capture_event)

    def _capture_event(self, topic: str, data: Any):
        event = {
            "topic": topic,
            "data": data,
            "timestamp": time.time(),
        }
        if topic == "pet/emotion/changed" and isinstance(data, dict):
            event["mood"] = data.get("mood", "neutral")
        elif topic == "pet/ai/backend" and isinstance(data, dict):
            backend = str(data.get("backend", "unknown"))
            reason = str(data.get("reason", ""))
            self._last_ai_backend = backend
            self._last_ai_backend_reason = reason
            event["backend"] = backend
            event["reason"] = reason
        elif topic == "pet/ai/response" and isinstance(data, dict):
            event["text"] = data.get("text", "")
            event["intent"] = data.get("intent", "social")
        elif topic in {"pet/voice/transcript", "pet/input/wake_word"} and isinstance(data, dict):
            event["text"] = data.get("text", data.get("wake_word", ""))
        elif topic == "pet/sound/play" and isinstance(data, dict):
            event["sound"] = data.get("name", "")
        elif topic == "pet/system/tick" and isinstance(data, dict):
            event["tick_count"] = data.get("tick_count")

        with self._event_lock:
            if self._event_queue.full():
                try:
                    self._event_queue.get_nowait()
                except queue.Empty:
                    pass
            self._event_queue.put_nowait(event)

    def _cleanup(self):
        if self._cleanup_done:
            return
        self._cleanup_done = True

        for attr in ["_tts", "_stt", "_wake", "_brain", "_memory_manager", "_idle_tick", "_sound_engine", "_emotion_engine"]:
            if self.bus is not None and hasattr(self.bus, attr):
                try:
                    getattr(self.bus, attr).stop()
                except Exception:
                    pass

        if self.input_sim is not None:
            try:
                self.input_sim.stop()
            except Exception:
                pass

        if self.face_renderer is not None:
            try:
                self.face_renderer.stop()
            except Exception:
                pass

        if self.bus is not None:
            try:
                self.bus.shutdown()
            except Exception:
                pass

        if self.memory is not None:
            try:
                self.memory.close()
            except Exception:
                pass


def start_backend(config: dict[str, Any] | None = None, headless: bool = True) -> StreamlitBackend:
    backend = StreamlitBackend(config=config or load_config(), headless=headless)
    return backend.start()
