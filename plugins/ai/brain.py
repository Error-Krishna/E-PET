import json
import logging
import queue
import os
import threading
import time
import uuid

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
        self.request_timeout = ai_config.get("request_timeout", 60)
        self.fallback_response = "I'm sorry, I'm having trouble thinking right now."

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

    def _groq_available(self):
        if not REQUESTS_AVAILABLE or not self.groq_api_key:
            return False

        try:
            response = requests.get(GROQ_API_BASE_URL, timeout=min(3, float(self.request_timeout)))
            return response is not None
        except Exception:
            return False

    def start(self):
        if hasattr(self.bus, "_ai_queue"):
            logger.info("AI: request queue ready")
            return
        self._thread = threading.Thread(target=self._process_queue, daemon=True)
        self._thread.start()
        logger.info("AI: ready")

    def stop(self):
        self._running = False
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
        # Build context
        if hasattr(self.bus, "_memory_manager"):
            context = self.bus._memory_manager.get_context()
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
            if self.mode == "offline":
                logger.info("AI offline mode active; bypassing Groq inference")
                response = self._fallback_payload()
            elif self.mode == "auto":
                if self._groq_available():
                    response = self._groq_inference(prompt)
                else:
                    logger.info("Groq unavailable; using offline fallback")
                    response = self._fallback_payload()
            elif self.mode == "online":
                response = self._groq_inference(prompt)
            else:
                raise ValueError(f"Unknown AI mode: {self.mode}")
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
            return self._fallback_payload()
        if not self.groq_api_key:
            logger.error("Groq API key missing; using fallback response")
            return self._fallback_payload()
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
            return str(message.get("content", "")).strip()
        except Exception as e:
            logger.error(f"Groq LLM error: {e}")
            self.mode = "offline"
            return self._fallback_payload()
