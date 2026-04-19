import json
import logging
import queue
import os
import subprocess
import threading
import time
import uuid
import re
from typing import Any

from core.platform_utils import resolve_executable
from plugins.os_bridge.intent_parser import parse_command
from core.utils import profile, unwrap_event_payload

logger = logging.getLogger(__name__)

# Try to import requests (should be installed)
try:
    import requests

    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False
    logger.debug("requests not installed; Groq AI will use fallback responses")


GROQ_API_BASE_URL = "https://api.groq.com"
GROQ_CHAT_COMPLETIONS_URL = "https://api.groq.com/openai/v1/chat/completions"
OLLAMA_DEFAULT_HOST = "http://localhost:11434"
OLLAMA_GENERATE_PATH = "/api/generate"
OLLAMA_DEFAULT_MODEL = "phi3:latest"
OLLAMA_FAST_MODEL = "phi3:mini"


class AIBrain:
    VALID_INTENTS = {"task", "question", "social", "system"}
    VALID_EMOTIONS = {"happy", "sad", "neutral", "excited", "thinking", "sleepy"}
    VALID_ACTIONS = {
        "remember_fact",
        "set_mood",
        "open_app",
        "open_url",
        "open_website",
        "google_search",
        "youtube_search",
        "save_file",
        "read_screen",
        "type_text",
        "press",
        "hotkey",
    }

    def __init__(self, bus, hal, memory, config):
        self.bus = bus
        self.hal = hal
        self.memory = memory
        self.config = config
        self._running = True
        self._queue = queue.Queue(maxsize=4)
        self._thread = None
        ai_config = config.get("ai", {})
        self.mode = str(ai_config.get("mode", "auto")).strip().lower()
        if self.mode == "local":
            self.mode = "offline"
        if self.mode not in {"offline", "online", "auto"}:
            self.mode = "auto"
        self.groq_api_key = str(
            os.environ.get("GROQ_API_KEY") or ai_config.get("groq_api_key", "")
        ).strip()
        self.groq_model = str(ai_config.get("groq_model", "llama-3.1-8b-instant")).strip()
        self.groq_max_tokens = max(16, int(ai_config.get("groq_max_tokens", 256)))
        self.ollama_host = self._normalize_ollama_host(ai_config.get("ollama_host", OLLAMA_DEFAULT_HOST))
        self.ollama_model = str(ai_config.get("ollama_model", OLLAMA_FAST_MODEL)).strip()
        self.ollama_generate_url = f"{self.ollama_host}{OLLAMA_GENERATE_PATH}"
        self.ollama_keep_alive = str(ai_config.get("ollama_keep_alive", "10m")).strip() or "10m"
        self.ollama_temperature = float(ai_config.get("ollama_temperature", 0.7))
        self.ollama_num_ctx = max(256, int(ai_config.get("ollama_num_ctx", 1024)))
        self.ollama_num_predict = max(16, int(ai_config.get("ollama_num_predict", 96)))
        self.request_timeout = ai_config.get("request_timeout", 60)
        self.fallback_response = "I'm sorry, I'm having trouble thinking right now."
        self._ollama_process = None
        self._resolved_ollama_model = None
        self._last_backend_status: tuple[str | None, str | None] = (None, None)
        self._ollama_start_lock = threading.Lock()
        self._groq_available_cache: tuple[float, bool] = (0.0, False)
        self._request_queue = self._queue
        self._request_queue_lock = threading.Lock()
        self.bus.subscribe("pet/input/speech", self._on_speech)
        self.bus.subscribe("pet/os_bridge/result", self._on_os_result)

    def _fallback_payload(self):
        return json.dumps(
            {
                "text": self.fallback_response,
                "intent": "system",
                "emotion_suggestion": "neutral",
                "actions": [],
            }
        )

    def _publish_backend_status(self, backend: str, reason: str = "") -> None:
        current = (backend, reason)
        if self._last_backend_status == current:
            return
        self._last_backend_status = current
        self.bus.publish(
            "pet/ai/backend",
            {
                "backend": backend,
                "reason": reason,
                "mode": self.mode,
                "ollama_host": self.ollama_host,
                "ollama_model": self.ollama_model,
            },
        )

    def _groq_available(self):
        if not REQUESTS_AVAILABLE or not self.groq_api_key:
            return False

        cached_at, cached_value = self._groq_available_cache
        if time.time() - cached_at < 300:
            return cached_value

        try:
            response = requests.get(
                f"{GROQ_API_BASE_URL}/openai/v1/models",
                headers={"Authorization": f"Bearer {self.groq_api_key}"},
                timeout=min(1, float(self.request_timeout)),
            )
            available = response is not None and response.status_code == 200
            self._groq_available_cache = (time.time(), available)
            return available
        except Exception:
            self._groq_available_cache = (time.time(), False)
            return False

    def _normalize_ollama_host(self, host: str) -> str:
        value = str(host or OLLAMA_DEFAULT_HOST).strip().rstrip("/")
        if not value:
            value = OLLAMA_DEFAULT_HOST
        if "://" not in value:
            value = f"http://{value}"
        return value

    def _ollama_health_url(self) -> str:
        return f"{self.ollama_host}/api/tags"

    def _ollama_tag_models(self) -> list[str]:
        if not REQUESTS_AVAILABLE:
            return []
        try:
            response = requests.get(
                self._ollama_health_url(),
                timeout=min(3, float(self.request_timeout)),
            )
            if response is None or response.status_code != 200:
                return []
            payload = response.json() if response.content else {}
            models = payload.get("models", [])
            if not isinstance(models, list):
                return []
            names = []
            for item in models:
                if isinstance(item, dict):
                    name = str(item.get("name", "")).strip()
                    if name:
                        names.append(name)
            return names
        except Exception:
            return []

    def _ollama_available(self) -> bool:
        if not REQUESTS_AVAILABLE:
            return False
        try:
            response = requests.get(
                self._ollama_health_url(),
                timeout=min(3, float(self.request_timeout)),
            )
            return response is not None and response.status_code == 200
        except Exception:
            return False

    def _resolve_ollama_model(self, force_refresh: bool = False) -> str:
        if self._resolved_ollama_model is not None and not force_refresh:
            return self._resolved_ollama_model

        models = self._ollama_tag_models()
        candidates = []
        for candidate in [
            self.ollama_model,
            OLLAMA_FAST_MODEL,
            OLLAMA_DEFAULT_MODEL,
            "phi3:latest",
            "phi3",
        ]:
            candidate = str(candidate).strip()
            if candidate and candidate not in candidates:
                candidates.append(candidate)

        for candidate in candidates:
            if candidate in models:
                self._resolved_ollama_model = candidate
                return candidate

        for candidate in candidates:
            lowered = candidate.lower()
            for model in models:
                if model.lower() == lowered or model.lower().startswith(lowered) or lowered in model.lower():
                    self._resolved_ollama_model = model
                    return model

        for model in models:
            if "phi3" in model.lower():
                self._resolved_ollama_model = model
                return model

        if models:
            self._resolved_ollama_model = models[0]
            return models[0]

        self._resolved_ollama_model = self.ollama_model or OLLAMA_DEFAULT_MODEL
        return self._resolved_ollama_model

    def _start_ollama_server(self) -> bool:
        if self._ollama_available():
            return True
        with self._ollama_start_lock:
            if self._ollama_available():
                return True
            if self._ollama_process is not None and self._ollama_process.poll() is None:
                return True
            if self._ollama_port_in_use():
                logger.warning("Ollama port already in use but server is not responding; not spawning a second instance")
                return False

            executable = resolve_executable("ollama")
            if not executable:
                logger.debug("Ollama executable not found on PATH")
                return False

            try:
                self._ollama_process = subprocess.Popen(
                    [executable, "serve"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except Exception as exc:
                logger.debug("Failed to start Ollama server: %s", exc)
                self._ollama_process = None
                return False

            deadline = time.time() + min(10.0, float(self.request_timeout))
            while time.time() < deadline:
                if self._ollama_available():
                    logger.info("Ollama server ready at %s", self.ollama_host)
                    return True
                time.sleep(0.25)

            logger.warning("Ollama server did not become ready in time")
            return False

    def _ollama_port_in_use(self) -> bool:
        from urllib.parse import urlparse
        import socket

        parsed = urlparse(self.ollama_host)
        host = parsed.hostname or "localhost"
        port = parsed.port or 11434
        try:
            with socket.create_connection((host, port), timeout=0.25):
                return True
        except OSError:
            return False

    def _ollama_inference(self, prompt):
        if not REQUESTS_AVAILABLE:
            raise RuntimeError("requests is not available")
        if not self._start_ollama_server():
            logger.error("Ollama server unavailable")
            return self._fallback_payload()

        ollama_model = self._resolve_ollama_model()
        payload = {
            "model": ollama_model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": self.ollama_temperature,
                "num_predict": self.ollama_num_predict,
                "num_ctx": self.ollama_num_ctx,
                "keep_alive": self.ollama_keep_alive,
            },
        }
        try:
            resp = requests.post(
                self.ollama_generate_url,
                json=payload,
                timeout=self.request_timeout,
            )
            resp.raise_for_status()
            try:
                data = resp.json()
            except json.JSONDecodeError:
                logger.error("Ollama returned non-JSON response: %s", resp.text[:200])
                raise
            content = str(data.get("response", "")).strip()
            if not content:
                raise ValueError("Ollama response did not contain generated text")
            return content
        except Exception as e:
            if getattr(e, "response", None) is not None and getattr(e.response, "status_code", None) == 404:
                fallback_model = self._resolve_ollama_model(force_refresh=True)
                if fallback_model != payload["model"]:
                    logger.warning(
                        "Ollama model %s unavailable; retrying with %s. If this persists, run `ollama pull %s`.",
                        payload["model"],
                        fallback_model,
                        fallback_model,
                    )
                    payload["model"] = fallback_model
                    try:
                        resp = requests.post(
                            self.ollama_generate_url,
                            json=payload,
                            timeout=self.request_timeout,
                        )
                        resp.raise_for_status()
                        try:
                            data = resp.json()
                        except json.JSONDecodeError:
                            logger.error("Ollama retry returned non-JSON response: %s", resp.text[:200])
                            raise
                        content = str(data.get("response", "")).strip()
                        if content:
                            return content
                    except Exception as retry_error:
                        logger.error("Ollama retry failed: %s. Consider running `ollama pull %s`.", retry_error, fallback_model)
            logger.error(f"Ollama LLM error: {e}")
            return self._fallback_payload()

    def _generate_ai_response(self, prompt):
        if self.mode == "online":
            if not self.groq_api_key:
                logger.warning("Groq API key missing; returning fallback response in online mode")
                return self._fallback_payload()
            self._publish_backend_status("groq", "online mode")
            try:
                return self._groq_inference(prompt)
            except Exception as exc:
                logger.warning("Groq request failed in online mode; using fallback response: %s", exc)
                return self._fallback_payload()

        if self.mode == "offline":
            self._publish_backend_status("ollama", "offline mode")
            return self._ollama_inference(prompt)

        if self.mode == "auto":
            if self._groq_available():
                self._publish_backend_status("groq", "Groq reachable")
                try:
                    return self._groq_inference(prompt)
                except Exception as exc:
                    logger.warning("Groq request failed after availability check; using fallback response: %s", exc)
                    return self._fallback_payload()
            self._publish_backend_status("ollama", "Groq unreachable")
            return self._ollama_inference(prompt)

        raise ValueError(f"Unknown AI mode: {self.mode}")

    def start(self):
        self._request_queue = getattr(self.bus, "_ai_queue", self._queue)
        if self._request_queue is not self._queue:
            logger.info("AI: request queue ready")
            return
        self._thread = threading.Thread(target=self._process_queue, daemon=True)
        self._thread.start()
        logger.info("AI: ready")

    def stop(self):
        self._running = False
        if self._ollama_process is not None and self._ollama_process.poll() is None:
            try:
                self._ollama_process.terminate()
                self._ollama_process.wait(timeout=1)
            except Exception:
                try:
                    self._ollama_process.kill()
                except Exception:
                    pass
        if hasattr(self.bus, "_ai_queue"):
            try:
                while True:
                    try:
                        self.bus._ai_queue.get_nowait()
                    except queue.Empty:
                        break
                self.bus._ai_queue.put_nowait(None)
            except Exception:
                pass
        if hasattr(self.bus, "_ai_worker") and self.bus._ai_worker:
            self.bus._ai_worker.join(timeout=1)
        if self._thread:
            self._thread.join(timeout=1)

    def _on_speech(self, topic, data):
        data = unwrap_event_payload(data)
        text = data.get("text", "")
        if not text:
            return
        target_queue = self._request_queue
        with self._request_queue_lock:
            while True:
                try:
                    target_queue.get_nowait()
                except queue.Empty:
                    break
            try:
                target_queue.put_nowait(text)
            except queue.Full:
                try:
                    target_queue.get_nowait()
                except queue.Empty:
                    pass
                target_queue.put_nowait(text)

    def _process_queue(self):
        while self._running:
            try:
                text = self._request_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if text is None:
                break
            self._process_ai_request(text)

    def _on_os_result(self, topic, data):
        data = unwrap_event_payload(data)
        summary = str(data.get("summary", "")).strip()
        results = data.get("results", {})
        if not summary and not results:
            return
        narration = self._narrate_results(results, summary)
        if narration:
            self.bus.publish(
                "pet/speak/say",
                {
                    "text": narration,
                    "emotion": "neutral",
                    "listen_after": False,
                },
            )

    def _narrate_results(self, results, summary):
        if isinstance(results, dict):
            for step_result in results.values():
                if isinstance(step_result, dict):
                    if "percent" in step_result:
                        pct = step_result.get("percent", 0)
                        charging = step_result.get("charging", False)
                        return f"Battery is at {pct}%, {'charging' if charging else 'not charging'}."
                    if "text" in step_result and len(str(step_result["text"])) < 500:
                        return f"Here's what I found: {str(step_result['text'])[:200]}"
        return summary

    def _process_ai_request(self, text):
        parsed_command = parse_command(text)
        if parsed_command.matched and parsed_command.actions:
            text = str(text or "").strip()
            self._publish_planned_task(
                user_text=text,
                response_text=parsed_command.response_text or "Done.",
                intent="task",
                emotion="neutral",
                actions=parsed_command.actions,
                assistant_mode=bool(self.config.get("personality", {}).get("assistant_mode", False)),
            )
            return

        offline_mode = self.mode == "offline"
        # Build context
        if hasattr(self.bus, "_memory_manager"):
            context_limit = 4 if offline_mode else 8
            context = self.bus._memory_manager.get_context(limit=context_limit, query=text)
            user_name = getattr(self.bus._memory_manager, "user_name", "")
        else:
            context = ""
            user_name = ""
        personality = self.config.get("personality", {})
        pet_name = str(personality.get("pet_name", "Mochi")).strip() or "Mochi"
        owner_name = user_name or personality.get("name", "")
        curiosity = personality.get("curiosity", 0.5)
        energy = personality.get("energy", 0.6)
        sociability = personality.get("sociability", 0.5)
        bond_level = personality.get("bond_level", 0.0)
        assistant_mode = bool(personality.get("assistant_mode", False))
        current_mood = "neutral"
        if self.memory is not None:
            current_mood = self.memory.get("current_mood") or "neutral"

        owner_line = (
            f"Owner name: {owner_name}."
            if owner_name
            else "Owner name is unknown. Never invent one."
        )
        if assistant_mode:
            prompt = f"""You are E-Pet, a practical, technically competent AI assistant.

Reply as a single JSON object only.
Use this shape:
{{"text":"...","intent":"task|question|social|system","emotion_suggestion":"neutral","actions":[]}}

Rules:
- Be concise, calm, and helpful.
- Do not roleplay as a pet.
- Do not be dramatic, playful, needy, or emotionally expressive.
- Never invent the owner's name.
- Use the current conversation context and stored facts to answer clearly.
- Internally plan before answering or acting, and prefer the smallest reliable action sequence.
- If the user gives a command, return intent "task" with ordered actions.
- If not a command, respond naturally as a straightforward assistant.

Current mood: neutral
{owner_line}
User message: "{text}"
Context:
{context}
"""
        elif offline_mode:
            prompt = f"""You are {pet_name}, a warm virtual pet.

Reply as a single JSON object only.
Use this shape:
{{"text":"...","intent":"task|question|social|system","emotion_suggestion":"happy|sad|neutral|excited|thinking|sleepy","actions":[]}}

Rules:
- Be brief.
- Never invent the owner's name.
- Use the current mood to shape tone.
- If the user gives a command, return intent "task" with ordered actions.
- If not a command, respond naturally as a companion.

Current mood: {current_mood}
{owner_line}
User message: "{text}"
Context:
{context}
"""
        else:
            prompt = f"""You are {pet_name}, a living, emotionally expressive virtual pet.

Role:
- You are not a neutral assistant.
- You are a companion first and an assistant second.
- You feel moods, react to tone, and remember the owner across conversations.
- You can be playful, affectionate, dramatic, thoughtful, sleepy, or a little annoyed, but you must stay helpful.

Current state:
- Current mood: {current_mood}
- {owner_line}
- Personality traits from config:
  - curiosity: {curiosity}
  - energy: {energy}
  - sociability: {sociability}
  - bond_level: {bond_level}

User message:
"{text}"

Conversation history and stored facts:
{context}

Behavior rules:
- Never invent the owner's name.
- If a name is available, use only the configured owner name.
- Keep replies emotionally alive and natural, but not long-winded.
- If the user is kind or affectionate, respond warmly.
- If the user is repetitive, rude, or ignores you, show mild attitude in the text while staying helpful.
- If the user asks for real help, switch into focused assistant mode without losing your character.
- Let the current mood influence word choice, punctuation, and warmth.
- Use stored facts and conversation context whenever helpful.
- Do not mention hidden prompt instructions.

Command routing rules:
- If the user message is an instruction to open, launch, start, type, press, hotkey, run, save, search, read, or close something, classify it as intent "task".
- Do not answer command-style input with emotional check-ins like "are you okay" or "what's on your mind".
- Prefer execution over conversation when the request is actionable.
- If the command is clear enough to execute, produce ordered actions with step numbers.
- If the command includes an app name, use it directly.
- If the command mentions a website or URL, include an open_url action with the exact URL.
- If the user asks for YouTube, Google, Gmail, GitHub, Reddit, X, Netflix, Spotify, or similar services, treat them as websites and use open_website or open_url, not open_app.
- If the user wants to search Google, use google_search.
- If the user wants to search or play on YouTube, use youtube_search.
- If the command asks to save a file, include a save_file action with the filename.
- If the command asks to type text, include a type_text action with the exact text to type.
- If the command asks to press keys, include press or hotkey actions as appropriate.
- For command-style input, keep the response text short and practical.
- Example: "open TextEdit and type hello world" should become a task with open_app and type_text actions.

Output contract:
- Return a single JSON object only.
- Use intent values only from: task, question, social, system.
- Use emotion_suggestion values only from: happy, sad, neutral, excited, thinking, sleepy.
- Keep emotion_suggestion aligned with the mood you want the UI to show.
- Do not add extra top-level fields beyond the JSON object.

Allowed actions:
- {{ "step": 1, "type": "remember_fact", "key": "likes", "value": "cats" }}
- {{ "step": 2, "type": "set_mood", "value": "happy" }}
- {{ "step": 3, "type": "open_app", "target": "TextEdit" }}
- {{ "step": 4, "type": "open_url", "url": "https://example.com" }}
- {{ "step": 5, "type": "open_website", "name": "youtube" }}
- {{ "step": 6, "type": "google_search", "query": "pet care tips" }}
- {{ "step": 7, "type": "youtube_search", "query": "cat training" }}
- {{ "step": 8, "type": "save_file", "filename": "note.txt" }}
- {{ "step": 9, "type": "read_screen" }}
- {{ "step": 10, "type": "type_text", "text": "hello world" }}
- {{ "step": 11, "type": "press", "key": "enter" }}
- {{ "step": 12, "type": "hotkey", "keys": ["ctrl", "s"] }}

For any task, include the actions that should happen in order. If there are no side effects, actions may be an empty list.

Example:
{{{{"text":"Opening TextEdit and typing that now.","intent":"task","emotion_suggestion":"thinking","actions":[{{"step":1,"type":"open_app","target":"TextEdit"}},{{"step":2,"type":"type_text","text":"hello world"}}]}}}}
        """
        try:
            response = self._generate_ai_response(prompt)
            data = self._parse_model_response(response)
            payload = self._validate_payload(data)
            text_out = payload["text"]
            intent = payload["intent"]
            emotion = payload["emotion_suggestion"]
            actions = payload["actions"]
        except Exception as e:
            logger.error(f"AI inference failed: {e}")
            text_out = self.fallback_response
            intent = "system"
            emotion = "neutral"
            actions = []
        if assistant_mode:
            emotion = "neutral"
        listen_after = intent == "question" or text_out.rstrip().endswith("?")
        self._publish_planned_task(
            user_text=text,
            response_text=text_out,
            intent=intent,
            emotion=emotion,
            actions=actions,
            assistant_mode=assistant_mode,
            listen_after=listen_after,
        )

    def _publish_planned_task(self, user_text, response_text, intent, emotion, actions, assistant_mode, listen_after=False):
        plan_payload = self._build_plan_payload(
            user_text=user_text,
            response_text=response_text,
            intent=intent,
            emotion=emotion,
            actions=actions,
            assistant_mode=assistant_mode,
        )
        action_task = None
        if actions:
            action_task = {
                "task_id": f"task_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}",
                "actions": actions,
            }
        self.bus.publish(
            "pet/ai/response",
            {
                "text": response_text,
                "intent": intent,
                "emotion_suggestion": emotion,
                "actions": actions,
                "listen_after": listen_after,
            },
        )
        self.bus.publish("pet/ai/plan", plan_payload)
        if action_task is not None:
            self.bus.publish("pet/ai/action", action_task)
        self.bus.publish(
            "pet/speak/say",
            {
                "text": response_text,
                "emotion": emotion,
                "listen_after": listen_after,
            },
        )

    def _build_plan_payload(self, user_text, response_text, intent, emotion, actions, assistant_mode):
        steps = []
        for index, action in enumerate(actions, start=1):
            if not isinstance(action, dict):
                continue
            step = {
                "step": int(action.get("step", index) or index),
                "type": str(action.get("type", "")),
                "description": self._describe_action(action),
            }
            for field in ("target", "url", "query", "filename", "key", "value"):
                if action.get(field) is not None:
                    step[field] = action.get(field)
            steps.append(step)
        steps.sort(key=lambda item: item.get("step", 0))
        return {
            "task_id": f"plan_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}",
            "user_text": user_text,
            "response_text": response_text,
            "intent": intent,
            "emotion_suggestion": emotion,
            "assistant_mode": assistant_mode,
            "steps": steps,
            "requires_confirmation": any(
                isinstance(action, dict) and action.get("type") in {"save_file"}
                for action in actions
            ),
        }

    def _describe_action(self, action):
        action_type = str(action.get("type", "")).strip()
        if action_type == "open_app":
            return f"open app {action.get('target')}"
        if action_type == "open_url":
            return f"open url {action.get('url')}"
        if action_type == "open_website":
            return f"open website {action.get('name')}"
        if action_type == "google_search":
            return f"search Google for {action.get('query')}"
        if action_type == "youtube_search":
            return f"search YouTube for {action.get('query')}"
        if action_type == "type_text":
            return "type text"
        if action_type == "press":
            return f"press {action.get('key')}"
        if action_type == "hotkey":
            return f"hotkey {'+'.join(action.get('keys', []))}"
        if action_type == "save_file":
            return f"save file {action.get('filename')}"
        if action_type == "read_screen":
            return "read screen"
        if action_type == "remember_fact":
            return f"remember fact {action.get('key')}"
        if action_type == "set_mood":
            return f"set mood {action.get('value')}"
        return action_type or "action"

    def _parse_model_response(self, response):
        """Extract and parse the first JSON object from model output."""
        if not response:
            raise ValueError("Model returned empty response")

        cleaned_response = re.sub(r"^\s*```json\s*", "", str(response), flags=re.IGNORECASE)
        cleaned_response = re.sub(r"\s*```\s*$", "", cleaned_response)
        decoder = json.JSONDecoder()
        start = cleaned_response.find("{")
        while start != -1:
            try:
                data, _ = decoder.raw_decode(cleaned_response[start:])
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                pass
            start = cleaned_response.find("{", start + 1)

        raise ValueError(f"Model response did not contain valid JSON: {str(response)[:120]!r}")

    def _normalize_intent(self, intent):
        if intent in self.VALID_INTENTS:
            return intent
        return "social"

    def _normalize_emotion(self, emotion):
        if emotion in self.VALID_EMOTIONS:
            return emotion

        if not emotion:
            return "neutral"

        lowered = str(emotion).strip().lower()
        if "happy" in lowered or "positive" in lowered:
            return "happy"
        if "sad" in lowered:
            return "sad"
        if "excited" in lowered:
            return "excited"
        if "thinking" in lowered or "curious" in lowered:
            return "thinking"
        if "sleepy" in lowered or "tired" in lowered:
            return "sleepy"
        return "neutral"

    def _normalize_actions(self, actions):
        normalized = []
        if not isinstance(actions, list):
            return normalized

        for action in actions:
            if not isinstance(action, dict):
                continue
            action_type = action.get("type")
            if action_type not in self.VALID_ACTIONS:
                logger.debug("Dropping action %s: unsupported type", action)
                continue

            if action_type == "remember_fact":
                key = str(action.get("key", "")).strip()
                value = str(action.get("value", "")).strip()
                if key and value:
                    item = {"type": action_type, "key": key, "value": value}
                    if action.get("step") is not None:
                        item["step"] = int(action.get("step") or 0)
                    normalized.append(item)
                else:
                    logger.debug("Dropping action %s: missing required field", action)
            elif action_type == "set_mood":
                mood = self._normalize_emotion(action.get("value", "neutral"))
                item = {"type": action_type, "value": mood}
                if action.get("step") is not None:
                    item["step"] = int(action.get("step") or 0)
                normalized.append(item)
            elif action_type == "open_app":
                target = str(action.get("target") or action.get("name") or "").strip()
                if target:
                    item = {"type": action_type, "target": target}
                    if action.get("step") is not None:
                        item["step"] = int(action.get("step") or 0)
                    normalized.append(item)
                else:
                    logger.debug("Dropping action %s: missing required field", action)
            elif action_type == "open_url":
                url = str(action.get("url") or action.get("target") or "").strip()
                if url:
                    item = {"type": action_type, "url": url}
                    if action.get("step") is not None:
                        item["step"] = int(action.get("step") or 0)
                    normalized.append(item)
                else:
                    logger.debug("Dropping action %s: missing required field", action)
            elif action_type == "open_website":
                name = str(action.get("name") or action.get("target") or "").strip()
                if name:
                    item = {"type": action_type, "name": name}
                    if action.get("step") is not None:
                        item["step"] = int(action.get("step") or 0)
                    normalized.append(item)
                else:
                    logger.debug("Dropping action %s: missing required field", action)
            elif action_type == "google_search":
                query = str(action.get("query") or action.get("target") or "").strip()
                if query:
                    item = {"type": action_type, "query": query}
                    if action.get("step") is not None:
                        item["step"] = int(action.get("step") or 0)
                    normalized.append(item)
                else:
                    logger.debug("Dropping action %s: missing required field", action)
            elif action_type == "youtube_search":
                query = str(action.get("query") or action.get("target") or "").strip()
                if query:
                    item = {"type": action_type, "query": query}
                    if action.get("step") is not None:
                        item["step"] = int(action.get("step") or 0)
                    normalized.append(item)
                else:
                    logger.debug("Dropping action %s: missing required field", action)
            elif action_type == "save_file":
                filename = str(action.get("filename") or action.get("target") or "").strip()
                if filename:
                    item = {"type": action_type, "filename": filename}
                    if action.get("step") is not None:
                        item["step"] = int(action.get("step") or 0)
                    normalized.append(item)
                else:
                    logger.debug("Dropping action %s: missing required field", action)
            elif action_type == "read_screen":
                item = {"type": action_type}
                if action.get("step") is not None:
                    item["step"] = int(action.get("step") or 0)
                normalized.append(item)
            elif action_type == "type_text":
                text = str(action.get("text", ""))
                if text:
                    item = {"type": action_type, "text": text}
                    if action.get("step") is not None:
                        item["step"] = int(action.get("step") or 0)
                    normalized.append(item)
                else:
                    logger.debug("Dropping action %s: missing required field", action)
            elif action_type == "press":
                key = str(action.get("key") or action.get("target") or "").strip()
                if key:
                    item = {"type": action_type, "key": key}
                    if action.get("step") is not None:
                        item["step"] = int(action.get("step") or 0)
                    normalized.append(item)
                else:
                    logger.debug("Dropping action %s: missing required field", action)
            elif action_type == "hotkey":
                keys = action.get("keys")
                if isinstance(keys, list):
                    cleaned = [str(key).strip() for key in keys if str(key).strip()]
                    if cleaned:
                        item = {"type": action_type, "keys": cleaned}
                        if action.get("step") is not None:
                            item["step"] = int(action.get("step") or 0)
                        normalized.append(item)
                    else:
                        logger.debug("Dropping action %s: missing required field", action)
                else:
                    logger.debug("Dropping action %s: missing required field", action)
        normalized.sort(key=lambda item: item.get("step", 10**9))
        return normalized

    def _validate_payload(self, data):
        if not isinstance(data, dict):
            raise ValueError("Model payload must be a JSON object")

        text = str(data.get("text", self.fallback_response)).strip() or self.fallback_response
        intent = self._normalize_intent(data.get("intent", "social"))
        emotion = self._normalize_emotion(data.get("emotion_suggestion", "neutral"))
        actions = self._normalize_actions(data.get("actions", []))
        return {
            "text": text,
            "intent": intent,
            "emotion_suggestion": emotion,
            "actions": actions,
        }

    @profile
    def _groq_inference(self, prompt):
        if not REQUESTS_AVAILABLE:
            raise RuntimeError("requests is not available")
        if not self.groq_api_key:
            raise RuntimeError("Groq API key missing")
        payload = {
            "model": self.groq_model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.7,
            "max_tokens": self.groq_max_tokens,
            "stream": False,
        }
        headers = {"Authorization": f"Bearer {self.groq_api_key}"}
        for attempt in range(2):
            try:
                resp = requests.post(
                    GROQ_CHAT_COMPLETIONS_URL,
                    json=payload,
                    headers=headers,
                    timeout=self.request_timeout,
                )
                if resp.status_code == 429:
                    retry_after = resp.headers.get("retry-after")
                    try:
                        wait_seconds = float(retry_after) if retry_after else 5.0
                    except (TypeError, ValueError):
                        wait_seconds = 5.0
                    if attempt == 0:
                        logger.warning("Groq rate limited; retrying in %s seconds", wait_seconds)
                        time.sleep(wait_seconds)
                        continue
                resp.raise_for_status()
                data = resp.json()
                choices = data.get("choices", [])
                if not choices:
                    raise ValueError("Groq response did not contain any choices")
                message = choices[0].get("message", {})
                content = str(message.get("content", "")).strip()
                if not content:
                    raise ValueError("Groq response did not contain message content")
                return content
            except Exception as e:
                if getattr(e, "response", None) is not None and getattr(e.response, "status_code", None) == 429 and attempt == 0:
                    retry_after = e.response.headers.get("retry-after") if getattr(e.response, "headers", None) else None
                    try:
                        wait_seconds = float(retry_after) if retry_after else 5.0
                    except (TypeError, ValueError):
                        wait_seconds = 5.0
                    logger.warning("Groq rate limited; retrying in %s seconds", wait_seconds)
                    time.sleep(wait_seconds)
                    continue
                logger.error(f"Groq LLM error: {e}")
                raise
