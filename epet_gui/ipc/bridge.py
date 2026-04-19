from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Dict

import yaml

from core.platform_utils import get_project_root

STATE_FILENAME = ".epet_state.json"
COMMAND_FILENAME = ".epet_cmd.json"
DEFAULT_LOG_FILENAME = "epet.log"


def project_root() -> Path:
    return get_project_root()


def _ipc_base_dir(root: Path | None = None) -> Path:
    env_root = os.environ.get("EPET_STATE_DIR", "").strip()
    if env_root:
        return Path(env_root).expanduser()
    return root or project_root()


def state_file_path(root: Path | None = None) -> Path:
    # File-based IPC only works when the GUI and backend share the same
    # filesystem. A socket transport is needed for multi-machine support.
    return _ipc_base_dir(root) / STATE_FILENAME


def command_file_path(root: Path | None = None) -> Path:
    # Same shared-filesystem requirement as state_file_path().
    return _ipc_base_dir(root) / COMMAND_FILENAME


def resolve_log_file_path(config: dict[str, Any] | None = None, config_path: Path | None = None) -> Path:
    logging_config = (config or {}).get("logging", {})
    raw = str(logging_config.get("file", DEFAULT_LOG_FILENAME)).strip() or DEFAULT_LOG_FILENAME
    path = Path(raw).expanduser()
    if path.is_absolute():
        return path
    base = config_path.parent if config_path else project_root()
    return base / path


def read_json_file(path: Path) -> Dict[str, Any] | None:
    try:
        if not path.exists():
            return None
        if path.stat().st_size == 0:
            return None
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def write_json_atomic(path: Path, data: Dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=False)
            handle.write("\n")
        attempts = 3 if os.name == "nt" else 1
        last_error = None
        for attempt in range(1, attempts + 1):
            try:
                os.replace(tmp_path, path)
                last_error = None
                break
            except PermissionError as exc:
                last_error = exc
                if attempt < attempts:
                    time.sleep(0.05)
        if last_error is not None:
            raise last_error
    except Exception:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        raise


def write_yaml_atomic(path: Path, data: Dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            yaml.safe_dump(data, handle, sort_keys=False)
        attempts = 3 if os.name == "nt" else 1
        last_error = None
        for attempt in range(1, attempts + 1):
            try:
                os.replace(tmp_path, path)
                last_error = None
                break
            except PermissionError as exc:
                last_error = exc
                if attempt < attempts:
                    time.sleep(0.05)
        if last_error is not None:
            raise last_error
    except Exception:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        raise


def remove_file_safely(path: Path) -> None:
    try:
        Path(path).unlink(missing_ok=True)
    except Exception:
        pass


def collect_memory_stats(memory) -> Dict[str, Any]:
    stats: Dict[str, Any] = {
        "facts_total": 0,
        "conversation_count": 0,
        "event_count": 0,
        "memory_count": 0,
        "interaction_count": 0,
    }
    if memory is None:
        return stats

    try:
        if hasattr(memory, "stats"):
            stats.update(memory.stats())
            return stats

        if hasattr(memory, "export"):
            exported = memory.export()
            if isinstance(exported, dict):
                kv = exported.get("kv", {})
                memories = exported.get("memories", {})
                events = exported.get("events", [])
                stats["facts_total"] = len((memories or {}).get("facts", {})) if isinstance(memories, dict) else 0
                stats["event_count"] = len(events) if isinstance(events, list) else 0
                stats["memory_count"] = (
                    sum(len(items) for items in memories.values()) + len(kv)
                    if isinstance(memories, dict) and isinstance(kv, dict)
                    else 0
                )
                current_mood = kv.get("current_mood") if isinstance(kv, dict) else None
                if current_mood:
                    stats["current_mood"] = current_mood
                if isinstance(memories, dict):
                    conversation = memories.get("conversation", {})
                    if isinstance(conversation, dict):
                        raw_history = conversation.get("history")
                        if raw_history:
                            try:
                                history = json.loads(raw_history)
                                if isinstance(history, list):
                                    stats["conversation_count"] = len(history)
                            except Exception:
                                pass
                    personality = memories.get("personality", {})
                    if isinstance(personality, dict):
                        raw_count = personality.get("interaction_count")
                        if raw_count:
                            try:
                                stats["interaction_count"] = int(raw_count)
                            except Exception:
                                pass
                return stats
    except Exception:
        return stats
    return stats


def build_runtime_state(
    *,
    running: bool,
    mood: str,
    ai_mode: str,
    voice_state: str,
    last_speech: str,
    last_response: str,
    start_time: float,
    plugins: Dict[str, bool],
    memory,
    last_event: str = "",
    ai_backend: str | None = None,
) -> Dict[str, Any]:
    stats = collect_memory_stats(memory)
    return {
        "running": bool(running),
        "mood": mood,
        "ai_mode": ai_mode,
        "ai_backend": ai_backend or ai_mode,
        "voice_state": voice_state,
        "last_speech": last_speech,
        "last_response": last_response,
        "last_event": last_event,
        "uptime_seconds": max(0, int(time.time() - start_time)),
        "plugins": dict(plugins),
        "memory": stats,
        "timestamp": time.time(),
    }


def dispatch_command(bus, payload: Dict[str, Any], logger=None) -> str:
    command = str(payload.get("command", "")).strip()
    args = payload.get("args", {}) if isinstance(payload.get("args", {}), dict) else {}
    if not command:
        return "missing_command"

    if command == "set_mood":
        mood = str(args.get("mood", "")).strip()
        if mood:
            bus.publish("pet/ai/action", {"type": "set_mood", "value": mood})
            return "ok"
        return "missing_mood"

    if command == "speak":
        text = str(args.get("text", "")).strip()
        if text:
            if logger and hasattr(bus, "has_subscribers") and not bus.has_subscribers("pet/speak/say"):
                logger.warning("No subscribers for pet/speak/say; speech command will be dropped")
            bus.publish("pet/speak/say", {"text": text, "emotion": args.get("emotion", "neutral"), "listen_after": bool(args.get("listen_after", False))})
            return "ok"
        return "missing_text"

    if command == "inject_speech":
        text = str(args.get("text", "")).strip()
        if text:
            if logger and hasattr(bus, "has_subscribers") and not bus.has_subscribers("pet/input/speech"):
                logger.warning("No subscribers for pet/input/speech; injected speech will be dropped")
            bus.publish("pet/input/speech", {"text": text, "confidence": 1.0, "source": args.get("source", "gui")})
            return "ok"
        return "missing_text"

    if command == "trigger_wake":
        bus.publish("pet/input/wake_word", {"source": args.get("source", "gui"), "wake_word": args.get("wake_word", "hey pip")})
        return "ok"

    if command == "publish_event":
        topic = str(args.get("topic", "")).strip()
        data = args.get("data", {})
        if topic:
            bus.publish(topic, data)
            return "ok"
        return "missing_topic"

    if command == "reload_config":
        if logger:
            logger.info("GUI requested config reload; restart the pet process to apply changes")
        # Hot reload could re-read config.yaml and reinitialize affected
        # plugins, but the current runtime treats restart as the safe path.
        return "ok"

    if command == "quit":
        bus.publish("pet/system/quit", {"source": args.get("source", "gui")})
        return "ok"

    if logger:
        logger.warning("Unknown GUI command received: %s", command)
    return "unknown_command"
