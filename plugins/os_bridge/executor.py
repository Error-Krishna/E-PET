import logging
import queue
import threading
import time
from typing import Any, Dict

from .actions.apps import open_app
from .actions.keyboard import hotkey, press, type_text

logger = logging.getLogger(__name__)


class OSBridgeExecutor:
    SUPPORTED_ACTIONS = {"open_app", "type_text", "press", "hotkey"}

    def __init__(self, bus, hal, memory, config):
        self.bus = bus
        self.hal = hal
        self.memory = memory
        self.config = config
        self._running = False
        self._thread = None
        os_config = config.get("os_bridge", {})
        self.delay_between_actions = max(0.0, float(os_config.get("delay_between_actions", 0.3)))
        self._queue: "queue.Queue[dict[str, Any] | None]" = queue.Queue()

    def start(self):
        if self._running:
            return
        self._running = True
        self.bus.subscribe("pet/ai/action", self._on_action)
        self._thread = threading.Thread(target=self._run, daemon=True, name="epet-os-bridge")
        self._thread.start()
        logger.info("OS bridge executor started")

    def stop(self):
        self._running = False
        try:
            self._queue.put_nowait(None)
        except Exception:
            pass
        if self._thread:
            self._thread.join(timeout=1)

    def _on_action(self, topic: str, data: Any):
        payload = self._normalize_payload(data)
        if not payload:
            return
        try:
            self._queue.put_nowait(payload)
        except queue.Full:
            logger.warning("[ERROR] OS bridge queue is full; dropping task")

    def _run(self):
        while self._running:
            try:
                payload = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue

            if payload is None:
                break

            self._execute_task(payload)

    def _normalize_payload(self, data: Any) -> Dict[str, Any] | None:
        if not isinstance(data, dict):
            return None

        payload = data.get("data") if isinstance(data.get("data"), dict) else data
        if not isinstance(payload, dict):
            return None

        actions = payload.get("actions")
        if actions is None and payload.get("type"):
            actions = [payload]
        if isinstance(actions, dict):
            actions = [actions]
        if not isinstance(actions, list):
            return None

        supported_actions = [action for action in actions if isinstance(action, dict) and action.get("type") in self.SUPPORTED_ACTIONS]
        unsupported_actions = [action for action in actions if not isinstance(action, dict) or action.get("type") not in self.SUPPORTED_ACTIONS]

        for action in unsupported_actions:
            action_type = action.get("type") if isinstance(action, dict) else None
            logger.debug("[OS] ignoring unsupported action %s", action_type)

        if not supported_actions:
            return None

        task_id = str(payload.get("task_id") or data.get("task_id") or f"task_{int(time.time() * 1000)}")
        return {
            "task_id": task_id,
            "actions": supported_actions,
        }

    def _execute_task(self, payload: Dict[str, Any]):
        task_id = payload["task_id"]
        actions = sorted(
            payload.get("actions", []),
            key=lambda action: int(action.get("step", 0) or 0),
        )
        if not actions:
            return

        for index, action in enumerate(actions, start=1):
            step = int(action.get("step", index) or index)
            try:
                self._execute_action(action)
                logger.info("[TASK] task=%s step=%s completed", task_id, step)
                self._publish_status(task_id, step, "completed")
            except Exception as exc:
                logger.error("[ERROR] task=%s step=%s failed: %s", task_id, step, exc, exc_info=True)
                self._publish_status(task_id, step, "failed", str(exc))

            if index < len(actions):
                time.sleep(self.delay_between_actions)

    def _execute_action(self, action: Dict[str, Any]):
        action_type = action.get("type")
        if action_type == "open_app":
            target = str(action.get("target") or action.get("name") or "").strip()
            if not target:
                raise ValueError("open_app requires a target app name")
            open_app(target)
            return

        if action_type == "type_text":
            text = str(action.get("text", ""))
            type_text(text)
            return

        if action_type == "press":
            key = str(action.get("key") or action.get("target") or "").strip()
            if not key:
                raise ValueError("press requires a key")
            press(key)
            return

        if action_type == "hotkey":
            keys = action.get("keys")
            if isinstance(keys, (list, tuple)):
                key_tuple = tuple(str(key).strip() for key in keys if str(key).strip())
            else:
                combo = str(action.get("target") or action.get("key") or "").strip()
                key_tuple = tuple(part.strip() for part in combo.replace("+", " ").split() if part.strip())
            if not key_tuple:
                raise ValueError("hotkey requires at least one key")
            hotkey(*key_tuple)
            return

        raise ValueError(f"Unsupported OS action type: {action_type}")

    def _publish_status(self, task_id: str, step: int, status: str, error: str | None = None):
        payload = {
            "task_id": task_id,
            "step": step,
            "status": status,
        }
        if error:
            payload["error"] = error
        self.bus.publish("pet/task/status", payload)
