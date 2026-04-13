import json
import logging
import queue
import os
import subprocess
import threading
import time
import uuid

from core.platform_utils import resolve_executable
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
OLLAMA_DEFAULT_MODEL = "phi3-latest"
OLLAMA_FAST_MODEL = "phi3:mini"


class AIBrain:
    VALID_INTENTS = {"task", "question", "social", "system"}
    VALID_EMOTIONS = {"happy", "sad", "neutral", "excited", "thinking", "sleepy"}
    VALID_ACTIONS = {"remember_fact", "set_mood", "open_app", "type_text", "press", "hotkey"}

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

        # Access to memory manager through bus? We'll use direct memory for now.
        # We'll assume memory manager is started and accessible via bus._memory_manager.
        self.memory_manager = None
        self.bus.subscribe("pet/input/speech", self._on_speech)
        self.bus.subscribe("pet/ai/response", self._on_response)  # for chain? Not needed

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

        try:
            response = requests.get(GROQ_API_BASE_URL, timeout=min(3, float(self.request_timeout)))
            return response is not None
        except Exception:
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
            data = resp.json()
            content = str(data.get("response", "")).strip()
            if not content:
                raise ValueError("Ollama response did not contain generated text")
            return content
        except Exception as e:
            if getattr(e, "response", None) is not None and getattr(e.response, "status_code", None) == 404:
                fallback_model = self._resolve_ollama_model(force_refresh=True)
                if fallback_model != payload["model"]:
                    logger.warning("Ollama model %s unavailable; retrying with %s", payload["model"], fallback_model)
                    payload["model"] = fallback_model
                    try:
                        resp = requests.post(
                            self.ollama_generate_url,
                            json=payload,
                            timeout=self.request_timeout,
                        )
                        resp.raise_for_status()
                        data = resp.json()
                        content = str(data.get("response", "")).strip()
                        if content:
                            return content
                    except Exception as retry_error:
                        logger.error(f"Ollama retry failed: {retry_error}")
            logger.error(f"Ollama LLM error: {e}")
            return self._fallback_payload()

    def _generate_ai_response(self, prompt):
        if self.mode == "online":
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
        if hasattr(self.bus, "_ai_queue"):
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
        target_queue = getattr(self.bus, "_ai_queue", self._queue)
        try:
            try:
                target_queue.get_nowait()
            except queue.Empty:
                pass
            target_queue.put_nowait(text)
        except queue.Full:
            try:
                target_queue.get_nowait()
            except queue.Empty:
                pass
            target_queue.put_nowait(text)

    def _on_response(self, topic, data):
        return None

    def _process_queue(self):
        while self._running:
            try:
                text = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if text is None:
                break
            self._process_ai_request(text)

    def _process_ai_request(self, text):
        offline_mode = self.mode == "offline"
        # Build context
        if hasattr(self.bus, "_memory_manager"):
            context_limit = 4 if offline_mode else 8
            context = self.bus._memory_manager.get_context(limit=context_limit)
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
        current_mood = "neutral"
        if self.memory is not None:
            current_mood = self.memory.get("current_mood") or "neutral"

        owner_line = (
            f"Owner name: {owner_name}."
            if owner_name
            else "Owner name is unknown. Never invent one."
        )
        if offline_mode:
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
- If the user message is an instruction to open, launch, start, type, press, hotkey, run, save, search, or close something, classify it as intent "task".
- Do not answer command-style input with emotional check-ins like "are you okay" or "what's on your mind".
- Prefer execution over conversation when the request is actionable.
- If the command is clear enough to execute, produce ordered actions with step numbers.
- If the command includes an app name, use it directly.
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
- {{ "step": 4, "type": "type_text", "text": "hello world" }}
- {{ "step": 5, "type": "press", "key": "enter" }}
- {{ "step": 6, "type": "hotkey", "keys": ["ctrl", "s"] }}

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
        listen_after = intent == "question" or text_out.rstrip().endswith("?")
        action_task = None
        if actions:
            action_task = {
                "task_id": f"task_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}",
                "actions": actions,
            }
        # Publish response
        self.bus.publish(
            "pet/ai/response",
            {
                "text": text_out,
                "intent": intent,
                "emotion_suggestion": emotion,
                "actions": actions,
                "listen_after": listen_after,
            },
        )
        if action_task is not None:
            self.bus.publish("pet/ai/action", action_task)
        # Also publish speak request
        self.bus.publish(
            "pet/speak/say",
            {
                "text": text_out,
                "emotion": emotion,
                "listen_after": listen_after,
            },
        )

    def _parse_model_response(self, response):
        """Extract and parse the first JSON object from model output."""
        if not response:
            raise ValueError("Model returned empty response")

        decoder = json.JSONDecoder()
        start = response.find("{")
        while start != -1:
            try:
                data, _ = decoder.raw_decode(response[start:])
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                pass
            start = response.find("{", start + 1)

        raise ValueError(f"Model response did not contain valid JSON: {response[:120]!r}")

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
                continue

            if action_type == "remember_fact":
                key = str(action.get("key", "")).strip()
                value = str(action.get("value", "")).strip()
                if key and value:
                    item = {"type": action_type, "key": key, "value": value}
                    if action.get("step") is not None:
                        item["step"] = int(action.get("step") or 0)
                    normalized.append(item)
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
            elif action_type == "type_text":
                text = str(action.get("text", ""))
                if text:
                    item = {"type": action_type, "text": text}
                    if action.get("step") is not None:
                        item["step"] = int(action.get("step") or 0)
                    normalized.append(item)
            elif action_type == "press":
                key = str(action.get("key") or action.get("target") or "").strip()
                if key:
                    item = {"type": action_type, "key": key}
                    if action.get("step") is not None:
                        item["step"] = int(action.get("step") or 0)
                    normalized.append(item)
            elif action_type == "hotkey":
                keys = action.get("keys")
                if isinstance(keys, list):
                    cleaned = [str(key).strip() for key in keys if str(key).strip()]
                    if cleaned:
                        item = {"type": action_type, "keys": cleaned}
                        if action.get("step") is not None:
                            item["step"] = int(action.get("step") or 0)
                        normalized.append(item)
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
            "max_tokens": 120,
            "stream": False,
        }
        try:
            headers = {"Authorization": f"Bearer {self.groq_api_key}"}
            resp = requests.post(
                GROQ_CHAT_COMPLETIONS_URL,
                json=payload,
                headers=headers,
                timeout=self.request_timeout,
            )
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
            logger.error(f"Groq LLM error: {e}")
            raise
