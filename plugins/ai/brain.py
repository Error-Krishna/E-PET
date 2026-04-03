import json
import logging
import queue
import threading

logger = logging.getLogger(__name__)

# Try to import requests (should be installed)
try:
    import requests

    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False
    logger.warning("requests not installed; AI will use fallback responses")


class AIBrain:
    VALID_INTENTS = {"task", "question", "social", "system"}
    VALID_EMOTIONS = {"happy", "sad", "neutral", "excited", "thinking", "sleepy"}
    VALID_ACTIONS = {"remember_fact", "set_mood"}

    def __init__(self, bus, hal, memory, config):
        self.bus = bus
        self.hal = hal
        self.memory = memory
        self.config = config
        self._running = True
        self._queue = queue.Queue(maxsize=4)
        self._thread = None
        ai_config = config.get("ai", {})
        self.mode = ai_config.get("mode", "local")
        self.model = ai_config.get("model", "phi3")
        self.api_key = ai_config.get("api_key", "")
        self.local_url = ai_config.get("local_url", "http://localhost:11434/api/generate")
        self.online_url = ai_config.get("online_url", "https://api.openai.com/v1/chat/completions")
        self.online_model = ai_config.get("online_model", "gpt-3.5-turbo")
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

    def start(self):
        # Wait for memory manager (it might start later)
        self._thread = threading.Thread(target=self._process_queue)
        self._thread.daemon = True
        self._thread.start()
        logger.info("AI brain started")

    def stop(self):
        self._running = False

    def _on_speech(self, topic, data):
        text = data.get("text", "")
        if not text:
            return
        try:
            self._queue.put_nowait(text)
        except queue.Full:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            self._queue.put_nowait(text)

    def _on_response(self, topic, data):
        return None

    def _process_queue(self):
        while self._running:
            try:
                text = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            self._handle_input(text)

    def _handle_input(self, text):
        # Build context
        if hasattr(self.bus, "_memory_manager"):
            context = self.bus._memory_manager.get_context()
        else:
            context = ""
        # Build prompt
        prompt = f"""You are an AI assistant for an interactive pet. The user just said: "{text}".

Context from previous conversation and stored facts:
{context}

Your response should be concise and friendly. Also classify the intent into one of: task, question, social, system.
Return a JSON object with fields:
- "text": string
- "intent": one of task, question, social, system
- "emotion_suggestion": one of happy, sad, neutral, excited, thinking, sleepy
- "actions": optional list of action objects

Allowed actions:
- {{"type": "remember_fact", "key": "name", "value": "Alice"}}
- {{"type": "remember_fact", "key": "likes", "value": "cats"}}
- {{"type": "set_mood", "value": "happy"}}

Example:
{{"text": "Hello!", "intent": "social", "emotion_suggestion": "happy", "actions": []}}
"""
        try:
            if self.mode == "local":
                response = self._local_inference(prompt)
            elif self.mode == "online":
                response = self._online_inference(prompt)
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
        # Publish response
        self.bus.publish(
            "pet/ai/response",
            {
                "text": text_out,
                "intent": intent,
                "emotion_suggestion": emotion,
                "actions": actions,
            },
        )
        for action in actions:
            self.bus.publish("pet/ai/action", action)
        # Also publish speak request
        self.bus.publish(
            "pet/speak/say",
            {
                "text": text_out,
                "emotion": emotion,
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
                    normalized.append({"type": action_type, "key": key, "value": value})
            elif action_type == "set_mood":
                mood = self._normalize_emotion(action.get("value", "neutral"))
                normalized.append({"type": action_type, "value": mood})
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

    def _local_inference(self, prompt):
        if not REQUESTS_AVAILABLE:
            return self._fallback_payload()
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
        }
        try:
            resp = requests.post(self.local_url, json=payload, timeout=self.request_timeout)
            resp.raise_for_status()
            data = resp.json()
            # Ollama returns a "response" field
            return data.get("response", "").strip()
        except Exception as e:
            logger.error(f"Local LLM error: {e}")
            return self._fallback_payload()

    def _online_inference(self, prompt):
        if not REQUESTS_AVAILABLE:
            return self._fallback_payload()
        headers = {"Authorization": f"Bearer {self.api_key}"}
        payload = {
            "model": self.online_model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.7,
            "max_tokens": 150,
        }
        try:
            resp = requests.post(self.online_url, json=payload, headers=headers, timeout=self.request_timeout)
            resp.raise_for_status()
            data = resp.json()
            # OpenAI format
            return data["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.error(f"Online LLM error: {e}")
            return self._fallback_payload()
