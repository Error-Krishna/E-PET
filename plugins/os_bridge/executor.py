import logging
import queue
import subprocess
import threading
import time
from typing import Any, Dict

from urllib.parse import quote_plus

from .actions.apps import open_app, open_url, resolve_open_app_candidates
from .actions.keyboard import hotkey, press, save_file, type_text
from .actions.screen import read_screen
from .actions.web import google_search, open_website, youtube_search

logger = logging.getLogger(__name__)


class OSBridgeExecutor:
    SUPPORTED_ACTIONS = {
        "open_app",
        "open_url",
        "open_website",
        "google_search",
        "youtube_search",
        "type_text",
        "press",
        "hotkey",
        "save_file",
        "read_screen",
    }
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
        self.verify_after_actions = bool(os_config.get("verify_after_actions", False))
        self.verification_delay = max(0.0, float(os_config.get("verification_delay", 0.75)))
        self._queue: "queue.Queue[dict[str, Any] | None]" = queue.Queue(maxsize=8)
        self._task_states: Dict[str, Dict[str, Any]] = {}
        self._state_lock = threading.Lock()
        self._action_registry = {
            "open_app": self._handle_open_app,
            "open_url": self._handle_open_url,
            "open_website": self._handle_open_website,
            "google_search": self._handle_google_search,
            "youtube_search": self._handle_youtube_search,
            "type_text": self._handle_type_text,
            "press": self._handle_press,
            "hotkey": self._handle_hotkey,
            "save_file": self._handle_save_file,
            "read_screen": self._handle_read_screen,
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
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
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
        decorated_actions = []
        for index, action in enumerate(payload.get("actions", []), start=1):
            if not isinstance(action, dict):
                continue
            raw_step = action.get("step")
            try:
                step_value = index if raw_step is None else int(raw_step or index)
            except (TypeError, ValueError):
                step_value = index
            decorated_actions.append((step_value, index, action))
        actions = [action for _, _, action in sorted(decorated_actions, key=lambda item: (item[0], item[1]))]
        if not actions:
            return

        self._update_task_state(task_id, "running", 0, "task started")
        logger.info("[TASK] started %s", task_id)
        self._publish_status(task_id, 0, "running", message="task started")
        task_failed = False
        step_results: Dict[int, Any] = {}

        for index, action in enumerate(actions, start=1):
            try:
                step = int(action.get("step", index) or index)
            except (TypeError, ValueError):
                step = index
            try:
                normalized_action = self._normalize_action(action, fallback_step=step)
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
            action_result = None
            attempts = self.max_retries + 1
            for attempt in range(1, attempts + 1):
                try:
                    action_result = self._execute_action(normalized_action, attempt=attempt)
                    self._verify_action(normalized_action, action_result)
                    success = True
                    break
                except ValueError as exc:
                    last_error = exc
                    break
                except Exception as exc:
                    last_error = exc
                    if not self._is_retryable_error(exc) or attempt > self.max_retries:
                        break
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
                self._update_task_state(task_id, "running", step, description, result=action_result)
                step_results[step] = action_result
                self._publish_status(task_id, step, "completed", message=description, result=action_result)
            else:
                error_text = str(last_error) if last_error else "step failed"
                logger.error("[ERROR] step failed task=%s step=%s: %s", task_id, step, error_text)
                task_failed = True
                self._update_task_state(task_id, "failed", step, error_text, result=action_result)
                step_results[step] = {"error": error_text, "result": action_result}
                self._publish_status(task_id, step, "failed", error_text, message=description, result=action_result)
                if not self.continue_on_failure:
                    break

            if index < len(actions):
                time.sleep(self.delay_between_actions)

        final_state = self.get_task_state(task_id)
        final_status = "failed" if task_failed else "completed"
        final_message = final_state.get("message", "task finished")
        self._update_task_state(
            task_id,
            final_status,
            final_state.get("current_step", len(actions)),
            final_message,
            result=final_state.get("result"),
        )
        self._publish_result(
            task_id=task_id,
            status=final_status,
            message=final_message,
            actions=actions,
            step_results=step_results,
            current_step=final_state.get("current_step", len(actions)),
            task_state=final_state,
        )

    def _normalize_action(self, action: Dict[str, Any], fallback_step: int = 0) -> Dict[str, Any]:
        action_type = str(action.get("type", "")).strip()
        if action_type not in self._action_registry:
            raise ValueError(f"Unsupported OS action type: {action_type}")

        normalized = dict(action)
        normalized["type"] = action_type
        step_value = normalized.get("step")
        if step_value is not None:
            normalized["step"] = int(step_value or 0)
        else:
            normalized["step"] = int(fallback_step or 0)
        if action_type == "open_app":
            target = str(action.get("target") or action.get("name") or "").strip()
            if not target:
                raise ValueError("open_app requires a target app name")
            normalized["target"] = target
            normalized["candidates"] = resolve_open_app_candidates(target)
        elif action_type == "open_url":
            url = str(action.get("url") or action.get("target") or "").strip()
            if not url:
                raise ValueError("open_url requires a url")
            normalized["url"] = url
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
                key_tuple = []
                for key in keys:
                    value = str(key).strip()
                    if not value:
                        continue
                    key_tuple.extend(part.strip() for part in value.replace("+", " ").split() if part.strip())
            else:
                combo = str(action.get("target") or action.get("key") or "").strip()
                key_tuple = [part.strip() for part in combo.replace("+", " ").split() if part.strip()]
            if not key_tuple:
                raise ValueError("hotkey requires at least one key")
            normalized["keys"] = key_tuple
        elif action_type == "save_file":
            filename = str(action.get("filename") or action.get("target") or "").strip()
            if not filename:
                raise ValueError("save_file requires a filename")
            normalized["filename"] = filename
        elif action_type == "read_screen":
            region = action.get("region")
            if region is not None:
                normalized["region"] = self._normalize_region(region)

        verification = self._normalize_verification(action)
        if verification is not None:
            normalized["verify"] = verification
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

        target = str(action.get("target") or action.get("name") or "").strip()
        routed_url = self._route_web_target(target)
        if routed_url:
            logger.info("[TASK] open_app routed to web target -> %s", routed_url)
            return open_url(routed_url)

        last_error = None
        for candidate in candidates:
            try:
                if candidate != target:
                    logger.info("[TASK] open_app fallback -> %s", candidate)
                open_app(candidate)
                return
            except Exception as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        raise RuntimeError(f"failed to launch app '{target}'")

    def _handle_open_url(self, action: Dict[str, Any], attempt: int = 1):
        url = str(action.get("url", "")).strip()
        if not url:
            raise ValueError("open_url requires a url")
        open_url(url)

    def _handle_open_website(self, action: Dict[str, Any], attempt: int = 1):
        name = str(action.get("name", "")).strip()
        if not name:
            raise ValueError("open_website requires a website name")
        open_website(name)

    def _handle_google_search(self, action: Dict[str, Any], attempt: int = 1):
        query = str(action.get("query", "")).strip()
        if not query:
            raise ValueError("google_search requires a query")
        google_search(query)

    def _handle_youtube_search(self, action: Dict[str, Any], attempt: int = 1):
        query = str(action.get("query", "")).strip()
        if not query:
            open_url("https://www.youtube.com")
            return
        youtube_search(query)

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

    def _handle_save_file(self, action: Dict[str, Any], attempt: int = 1):
        filename = str(action.get("filename", "")).strip()
        if not filename:
            raise ValueError("save_file requires a filename")
        save_file(filename)

    def _handle_read_screen(self, action: Dict[str, Any], attempt: int = 1):
        region = action.get("region")
        if region is not None:
            return read_screen(region=self._normalize_region(region))
        return read_screen()

    def _normalize_region(self, region: Any):
        if isinstance(region, dict):
            keys = ("left", "top", "width", "height")
            if all(key in region for key in keys):
                return tuple(int(region[key]) for key in keys)
        if isinstance(region, (list, tuple)) and len(region) == 4:
            return tuple(int(value) for value in region)
        raise ValueError("region must contain left, top, width, and height")

    def _normalize_verification(self, action: Dict[str, Any]) -> Dict[str, Any] | None:
        verify = action.get("verify")
        expected_text = str(action.get("expected_text", "")).strip()
        expected_contains = str(action.get("expected_contains", "")).strip()

        if verify is None and not expected_text and not expected_contains:
            return None

        normalized: Dict[str, Any] = {}
        if isinstance(verify, dict):
            verify_text = str(verify.get("expected_text") or verify.get("text") or "").strip()
            verify_contains = str(verify.get("expected_contains") or verify.get("contains") or "").strip()
            if verify_text:
                normalized["expected_text"] = verify_text
            if verify_contains:
                normalized["expected_contains"] = verify_contains
        elif isinstance(verify, str):
            normalized["expected_contains"] = verify.strip()
        elif verify:
            normalized["enabled"] = True

        if expected_text:
            normalized["expected_text"] = expected_text
        if expected_contains:
            normalized["expected_contains"] = expected_contains
        if not normalized:
            normalized["enabled"] = bool(verify)
        return normalized

    def _verify_action(self, action: Dict[str, Any], action_result: Any):
        verification = action.get("verify")
        action_type = action.get("type")
        if verification is None and action_type != "read_screen" and not self.verify_after_actions:
            return None

        if action_type == "read_screen":
            if not isinstance(verification, dict) or not verification:
                return action_result
            screen_text = str((action_result or {}).get("text", "")).strip()
            expected_text = str(verification.get("expected_text", "")).strip()
            expected_contains = str(verification.get("expected_contains", "")).strip()
            if expected_text and screen_text != expected_text:
                raise RuntimeError(f"screen text did not match expected text: {expected_text!r}")
            if expected_contains and expected_contains not in screen_text:
                raise RuntimeError(f"screen text did not contain expected text: {expected_contains!r}")
            return action_result

        if verification is None and self.verify_after_actions:
            verification = {"enabled": True}

        if not verification:
            return None

        if self.verification_delay > 0:
            time.sleep(self.verification_delay)

        region = action.get("region")
        try:
            if isinstance(region, (list, tuple, dict)):
                screen_result = read_screen(region=self._normalize_region(region))
            else:
                screen_result = read_screen()
        except Exception as exc:
            logger.warning("[OS] screen verification skipped: %s", exc)
            return None

        screen_text = str((screen_result or {}).get("text", "")).strip()
        expected_text = str(verification.get("expected_text", "")).strip()
        expected_contains = str(verification.get("expected_contains", "")).strip()
        if expected_text and screen_text != expected_text:
            raise RuntimeError(f"screen text did not match expected text: {expected_text!r}")
        if expected_contains and expected_contains not in screen_text:
            raise RuntimeError(f"screen text did not contain expected text: {expected_contains!r}")
        return {"text": screen_text}

    @staticmethod
    def _is_retryable_error(exc: Exception) -> bool:
        message = str(exc).lower()
        if any(
            marker in message
            for marker in (
                "unable to find application",
                "failed to launch app",
                "app not found",
                "launch failed",
            )
        ):
            return False
        retryable_types = (OSError, RuntimeError, subprocess.SubprocessError, subprocess.TimeoutExpired)
        return isinstance(exc, retryable_types)

    @staticmethod
    def _route_web_target(target: str) -> str | None:
        cleaned = str(target or "").strip().lower()
        if not cleaned:
            return None

        websites = {
            "youtube": "https://www.youtube.com",
            "google": "https://google.com",
            "gmail": "https://mail.google.com",
            "github": "https://github.com",
            "reddit": "https://reddit.com",
            "twitter": "https://twitter.com",
            "x": "https://x.com",
            "linkedin": "https://linkedin.com",
            "netflix": "https://netflix.com",
            "spotify": "https://open.spotify.com",
            "amazon": "https://amazon.com",
            "wikipedia": "https://wikipedia.org",
            "stack overflow": "https://stackoverflow.com",
            "chatgpt": "https://chat.openai.com",
            "claude": "https://claude.ai",
            "notion": "https://notion.so",
            "figma": "https://figma.com",
            "vercel": "https://vercel.com",
            "heroku": "https://heroku.com",
            "aws console": "https://console.aws.amazon.com",
            "google cloud": "https://console.cloud.google.com",
            "azure": "https://portal.azure.com",
        }
        if cleaned in websites:
            return websites[cleaned]

        if "youtube" in cleaned:
            query = cleaned.replace("youtube", "").replace("video", "").replace("watch", "").replace("play", "").strip(" -_.,")
            if query:
                return f"https://www.youtube.com/results?search_query={quote_plus(query)}"
            return "https://www.youtube.com"

        return None

    def _update_task_state(self, task_id: str, status: str, current_step: int, message: str, result: Any | None = None):
        state = {
            "task_id": task_id,
            "status": status,
            "current_step": int(current_step or 0),
            "message": message,
        }
        if result is not None:
            state["result"] = result
        with self._state_lock:
            self._task_states[task_id] = state

    def get_task_state(self, task_id: str) -> Dict[str, Any]:
        with self._state_lock:
            return dict(self._task_states.get(task_id, {}))

    def _describe_action(self, action: Dict[str, Any]) -> str:
        action_type = action.get("type")
        if action_type == "open_app":
            return f"opening {action.get('target')}"
        if action_type == "open_url":
            return f"opening url {action.get('url')}"
        if action_type == "type_text":
            return "typing text"
        if action_type == "press":
            return f"pressing {action.get('key')}"
        if action_type == "hotkey":
            return f"hotkey {'+'.join(action.get('keys', []))}"
        if action_type == "save_file":
            return f"saving file as {action.get('filename')}"
        if action_type == "read_screen":
            return "reading screen"
        return "running action"

    def _publish_status(self, task_id: str, step: int, status: str, error: str | None = None, message: str | None = None, result: Any | None = None):
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
        if result is not None:
            payload["result"] = result
        self.bus.publish("pet/task/status", payload)

    def _publish_result(
        self,
        *,
        task_id: str,
        status: str,
        message: str,
        actions: list[dict[str, Any]],
        step_results: Dict[int, Any],
        current_step: int,
        task_state: Dict[str, Any],
    ) -> None:
        payload = {
            "task_id": task_id,
            "status": status,
            "message": message,
            "summary": message,
            "actions": list(actions),
            "results": {str(step): result for step, result in step_results.items()},
            "current_step": int(current_step or 0),
            "task_state": dict(task_state or {}),
        }
        self.bus.publish("pet/os_bridge/result", payload)
