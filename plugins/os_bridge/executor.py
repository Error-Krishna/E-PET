import logging
import queue
import threading
import time
from typing import Any, Dict

from .actions.apps import open_app, resolve_open_app_candidates
from .actions.keyboard import hotkey, press, type_text

logger = logging.getLogger(__name__)


class OSBridgeExecutor:
    SUPPORTED_ACTIONS = {"open_app", "type_text", "press", "hotkey"}
    DEFAULT_MAX_RETRIES = 2

    def __init__(self, bus, hal, memory, config):
        self.bus = bus
        self.hal = hal
        self.memory = memory
        self.config = config
        self._running = False
        self._thread = None
        os_config = config.get("os_bridge", {})
        self.delay_between_actions = max(0.0, float(os_config.get("delay_between_actions", 0.3)))
        self.max_retries = max(0, int(os_config.get("max_retries", self.DEFAULT_MAX_RETRIES)))
        self.retry_delay = max(0.0, float(os_config.get("retry_delay", 0.15)))
        self.continue_on_failure = bool(os_config.get("continue_on_failure", False))
        self._queue: "queue.Queue[dict[str, Any] | None]" = queue.Queue()
        self._task_states: Dict[str, Dict[str, Any]] = {}
        self._state_lock = threading.Lock()
        self._action_registry = {
            "open_app": self._handle_open_app,
            "type_text": self._handle_type_text,
            "press": self._handle_press,
            "hotkey": self._handle_hotkey,
        }

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

        self._update_task_state(task_id, "running", 0, "task started")
        logger.info("[TASK] started %s", task_id)
        self._publish_status(task_id, 0, "running", message="task started")
        task_failed = False

        for index, action in enumerate(actions, start=1):
            step = int(action.get("step", index) or index)
            try:
                normalized_action = self._normalize_action(action)
            except Exception as exc:
                logger.error("[ERROR] task=%s step=%s failed: %s", task_id, step, exc)
                self._update_task_state(task_id, "failed", step, str(exc))
                self._publish_status(task_id, step, "failed", str(exc), message="validation failed")
                if not self.continue_on_failure:
                    break
                continue

            description = self._describe_action(normalized_action)
            self._update_task_state(task_id, "running", step, description)
            logger.info("[TASK] step %s running", step)
            self._publish_status(task_id, step, "running", message=description)

            success = False
            last_error = None
            attempts = self.max_retries + 1
            for attempt in range(1, attempts + 1):
                try:
                    self._execute_action(normalized_action, attempt=attempt)
                    success = True
                    break
                except Exception as exc:
                    last_error = exc
                    if attempt <= self.max_retries:
                        logger.info("[TASK] retry step %s", step)
                        self._publish_status(
                            task_id,
                            step,
                            "running",
                            message=f"retrying step {step}",
                        )
                        time.sleep(self.retry_delay)
                        continue
                    break

            if success:
                logger.info("[TASK] step %s completed", step)
                self._update_task_state(task_id, "running", step, description)
                self._publish_status(task_id, step, "completed", message=description)
            else:
                error_text = str(last_error) if last_error else "step failed"
                logger.error("[ERROR] step failed task=%s step=%s: %s", task_id, step, error_text)
                task_failed = True
                self._update_task_state(task_id, "failed", step, error_text)
                self._publish_status(task_id, step, "failed", error_text, message=description)
                if not self.continue_on_failure:
                    break

            if index < len(actions):
                time.sleep(self.delay_between_actions)

        final_state = self.get_task_state(task_id)
        final_status = "failed" if task_failed else "completed"
        final_message = final_state.get("message", "task finished")
        self._update_task_state(task_id, final_status, final_state.get("current_step", len(actions)), final_message)

    def _normalize_action(self, action: Dict[str, Any]) -> Dict[str, Any]:
        action_type = str(action.get("type", "")).strip()
        if action_type not in self._action_registry:
            raise ValueError(f"Unsupported OS action type: {action_type}")

        normalized = dict(action)
        normalized["type"] = action_type
        step_value = normalized.get("step")
        if step_value is not None:
            normalized["step"] = int(step_value or 0)
        else:
            normalized["step"] = 0
        if action_type == "open_app":
            target = str(action.get("target") or action.get("name") or "").strip()
            if not target:
                raise ValueError("open_app requires a target app name")
            normalized["target"] = target
            normalized["candidates"] = resolve_open_app_candidates(target)
        elif action_type == "type_text":
            text = str(action.get("text", ""))
            if not text:
                raise ValueError("type_text requires text")
            normalized["text"] = text
        elif action_type == "press":
            key = str(action.get("key") or action.get("target") or "").strip()
            if not key:
                raise ValueError("press requires a key")
            normalized["key"] = key
        elif action_type == "hotkey":
            keys = action.get("keys")
            if isinstance(keys, (list, tuple)):
                key_tuple = [str(key).strip() for key in keys if str(key).strip()]
            else:
                combo = str(action.get("target") or action.get("key") or "").strip()
                key_tuple = [part.strip() for part in combo.replace("+", " ").split() if part.strip()]
            if not key_tuple:
                raise ValueError("hotkey requires at least one key")
            normalized["keys"] = key_tuple
        return normalized

    def _execute_action(self, action: Dict[str, Any], attempt: int = 1):
        handler = self._action_registry.get(action.get("type"))
        if handler is None:
            raise ValueError(f"Unsupported OS action type: {action.get('type')}")
        return handler(action, attempt=attempt)

    def _handle_open_app(self, action: Dict[str, Any], attempt: int = 1):
        candidates = list(action.get("candidates") or [])
        if not candidates:
            candidates = resolve_open_app_candidates(action.get("target") or action.get("name") or "")
        if not candidates:
            raise ValueError("open_app requires a target app name")

        candidate_index = min(max(attempt - 1, 0), len(candidates) - 1)
        candidate = candidates[candidate_index]
        if candidate != action.get("target"):
            logger.info("[TASK] open_app fallback -> %s", candidate)
        open_app(candidate)

    def _handle_type_text(self, action: Dict[str, Any], attempt: int = 1):
        text = str(action.get("text", ""))
        if not text:
            raise ValueError("type_text requires text")
        type_text(text)

    def _handle_press(self, action: Dict[str, Any], attempt: int = 1):
        key = str(action.get("key", "")).strip()
        if not key:
            raise ValueError("press requires a key")
        press(key)

    def _handle_hotkey(self, action: Dict[str, Any], attempt: int = 1):
        keys = action.get("keys")
        if not isinstance(keys, (list, tuple)):
            raise ValueError("hotkey requires keys")
        cleaned = [str(key).strip() for key in keys if str(key).strip()]
        if not cleaned:
            raise ValueError("hotkey requires at least one key")
        hotkey(*cleaned)

    def _update_task_state(self, task_id: str, status: str, current_step: int, message: str):
        state = {
            "task_id": task_id,
            "status": status,
            "current_step": int(current_step or 0),
            "message": message,
        }
        with self._state_lock:
            self._task_states[task_id] = state

    def get_task_state(self, task_id: str) -> Dict[str, Any]:
        with self._state_lock:
            return dict(self._task_states.get(task_id, {}))

    def _describe_action(self, action: Dict[str, Any]) -> str:
        action_type = action.get("type")
        if action_type == "open_app":
            return f"opening {action.get('target')}"
        if action_type == "type_text":
            return "typing text"
        if action_type == "press":
            return f"pressing {action.get('key')}"
        if action_type == "hotkey":
            return f"hotkey {'+'.join(action.get('keys', []))}"
        return "running action"

    def _publish_status(self, task_id: str, step: int, status: str, error: str | None = None, message: str | None = None):
        payload = {
            "task_id": task_id,
            "step": step,
            "current_step": step,
            "status": status,
        }
        if message:
            payload["message"] = message
        if error:
            payload["error"] = error
        self.bus.publish("pet/task/status", payload)
