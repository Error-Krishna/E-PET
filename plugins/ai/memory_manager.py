import json
import logging
import re
import time

from core.utils import unwrap_event_payload

logger = logging.getLogger(__name__)


TOPIC_KEYWORDS = {
    "music": {"music", "song", "songs", "sing", "band", "album", "playlist"},
    "coding": {"code", "coding", "program", "programming", "python", "bug", "build", "app"},
    "games": {"game", "games", "gaming", "play", "minecraft", "roblox", "valorant"},
    "food": {"food", "eat", "eating", "hungry", "snack", "coffee", "tea"},
    "movies": {"movie", "movies", "film", "show", "series", "watch", "youtube"},
    "pets": {"pet", "pets", "dog", "cat", "animal", "puppy", "kitten"},
    "learning": {"study", "learn", "school", "college", "class", "exam", "homework"},
    "fitness": {"gym", "workout", "run", "running", "exercise", "fitness", "walk"},
    "art": {"draw", "drawing", "art", "design", "paint", "painting", "creative"},
    "work": {"work", "job", "office", "project", "meeting", "deadline"},
}


class MemoryManager:
    def __init__(self, bus, hal, memory, config):
        self.bus = bus
        self.hal = hal
        self.memory = memory
        self.config = config
        self._running = True
        self._history = []  # list of (role, text)
        self.memory_config = config.get("memory", {})
        self.max_history = self.memory_config.get("max_history", 20)
        self.persist_history = self.memory_config.get("persist_history", True)
        self.extract_facts = self.memory_config.get("extract_facts", True)
        self.user_name = str(config.get("personality", {}).get("name", "")).strip()
        self.assistant_mode = bool(config.get("personality", {}).get("assistant_mode", False))

    def start(self):
        self._load_persisted_history()
        if self.user_name:
            self.memory.remember("facts", "name", self.user_name)
        self.bus.subscribe("pet/input/speech", self._on_speech)
        self.bus.subscribe("pet/ai/response", self._on_response)
        self.bus.subscribe("pet/ai/action", self._on_action)
        self.bus.subscribe("pet/task/status", self._on_task_status)
        self.bus.subscribe("pet/os_bridge/result", self._on_task_result)
        logger.info("Memory: tracking conversation and facts")

    def stop(self):
        self._running = False

    def _on_speech(self, topic, data):
        data = unwrap_event_payload(data)
        text = data.get("text", "")
        self._add_to_history("user", text)
        self._extract_and_store_facts(text)
        self._learn_from_text(text)
        self._increment_interaction_count()

    def _on_response(self, topic, data):
        data = unwrap_event_payload(data)
        text = data.get("text", "")
        self._add_to_history("assistant", text)

    def _on_action(self, topic, data):
        data = unwrap_event_payload(data)
        actions = data.get("actions") if isinstance(data, dict) else None
        if actions is None:
            actions = [data] if isinstance(data, dict) else []
        for action in actions:
            if not isinstance(action, dict):
                continue
            if action.get("type") == "remember_fact":
                key = str(action.get("key", "")).strip()
                value = str(action.get("value", "")).strip()
                if key and value:
                    self.memory.remember("facts", key, value)

    def _on_task_status(self, topic, data):
        data = unwrap_event_payload(data)
        if not isinstance(data, dict):
            return
        task_id = str(data.get("task_id", "")).strip()
        status = str(data.get("status", "")).strip().lower()
        step = data.get("current_step")
        message = str(data.get("message", "")).strip()
        result = data.get("result")
        if not task_id or not status:
            return
        self.memory.remember("procedural", f"task:{task_id}:status", status)
        if step is not None:
            self.memory.remember("procedural", f"task:{task_id}:step", str(step))
        if message:
            self.memory.remember("procedural", f"task:{task_id}:message", message[:240])
        if isinstance(result, dict):
            self.memory.remember("procedural", f"task:{task_id}:result", json.dumps(result, ensure_ascii=False))

    def _on_task_result(self, topic, data):
        self.record_task_outcome(unwrap_event_payload(data))

    def record_task_outcome(self, payload):
        data = unwrap_event_payload(payload)
        if not isinstance(data, dict):
            return
        task_id = str(data.get("task_id", "")).strip()
        if not task_id:
            return
        status = str(data.get("status", "")).strip().lower() or "unknown"
        summary = str(data.get("summary", "")).strip()
        actions = data.get("actions", [])
        self.memory.remember("procedural", f"task:{task_id}:status", status)
        if summary:
            self.memory.remember("procedural", f"task:{task_id}:summary", summary[:240])
        if isinstance(actions, list):
            action_types = [str(action.get("type", "")).strip() for action in actions if isinstance(action, dict) and action.get("type")]
            if action_types:
                self.memory.remember("procedural", f"task:{task_id}:actions", ", ".join(action_types[:10]))
                for action_type in action_types:
                    self._update_procedural_counters(status=status, action_type=action_type, source="task_result")
        self._update_procedural_counters(status=status, source="task_result")

    def _add_to_history(self, role, text):
        if not text:
            return
        self._history.append({"role": role, "text": text, "timestamp": time.time()})
        if len(self._history) > self.max_history:
            self._history.pop(0)
        self._persist_history()

    def _persist_history(self):
        if self.persist_history:
            self.memory.remember("conversation", "history", json.dumps(self._history))

    def _load_persisted_history(self):
        if not self.persist_history:
            return
        raw = self.memory.recall("conversation", "history")
        if not raw:
            return
        try:
            history = json.loads(raw)
            if isinstance(history, list):
                self._history = history[-self.max_history :]
        except json.JSONDecodeError:
            logger.warning("Failed to load persisted conversation history")

    def get_recent_history(self, limit=8):
        """Return the most recent conversation turns in chronological order."""
        if limit <= 0:
            return []
        return self._history[-limit:]

    def _extract_and_store_facts(self, text):
        if not self.extract_facts:
            return

        if self.user_name:
            # Configured name is the source of truth; do not let speech overwrite it.
            self.memory.remember("facts", "name", self.user_name)
            like_match = re.search(r"\bI (?:like|love) ([A-Za-z0-9 ,'-]{1,60})", text.strip(), re.IGNORECASE)
            if like_match:
                self.memory.remember("facts", "likes", like_match.group(1).strip())
            return

        cleaned = text.strip()
        name_match = re.search(r"\bmy name is ([A-Za-z][A-Za-z' -]{0,40})", cleaned, re.IGNORECASE)
        like_match = re.search(r"\bI (?:like|love) ([A-Za-z0-9 ,'-]{1,60})", cleaned, re.IGNORECASE)

        if name_match:
            self.memory.remember("facts", "name", name_match.group(1).strip())
        if like_match:
            self.memory.remember("facts", "likes", like_match.group(1).strip())

    def _increment_interaction_count(self):
        raw = self.memory.recall("personality", "interaction_count")
        count = int(raw) if raw and raw.isdigit() else 0
        count += 1
        self.memory.remember("personality", "interaction_count", str(count))

        # Very simple progression signal for future personality features.
        bond_level = min(1.0, count / 50.0)
        self.memory.remember("personality", "bond_level", f"{bond_level:.2f}")

    def _learn_from_text(self, text):
        cleaned = str(text or "").strip().lower()
        if not cleaned:
            return

        matched_topics = []
        for topic, keywords in TOPIC_KEYWORDS.items():
            if any(keyword in cleaned for keyword in keywords):
                matched_topics.append(topic)

        if not matched_topics:
            self.memory.remember("personality", "last_topic", "general")
            return

        for topic in matched_topics:
            raw = self.memory.recall("personality", f"topic:{topic}")
            count = int(raw) if raw and str(raw).isdigit() else 0
            count += 1
            self.memory.remember("personality", f"topic:{topic}", str(count))
            self.memory.remember("personality", "last_topic", topic)

    def _update_procedural_counters(self, status: str, action_type: str | None = None, source: str = ""):
        outcome = "success" if status in {"completed", "success", "ok"} else "failure" if status in {"failed", "error"} else status or "unknown"
        key = f"procedural:{source or 'event'}:{outcome}"
        raw = self.memory.recall("procedural", key)
        count = int(raw) if raw and str(raw).isdigit() else 0
        self.memory.remember("procedural", key, str(count + 1))
        if action_type:
            action_key = f"procedural:action:{action_type}:{outcome}"
            raw_action = self.memory.recall("procedural", action_key)
            action_count = int(raw_action) if raw_action and str(raw_action).isdigit() else 0
            self.memory.remember("procedural", action_key, str(action_count + 1))

    def _learned_topics(self):
        topics = []
        for topic, _keywords in TOPIC_KEYWORDS.items():
            raw = self.memory.recall("personality", f"topic:{topic}")
            count = int(raw) if raw and str(raw).isdigit() else 0
            if count > 0:
                topics.append((topic, count))
        topics.sort(key=lambda item: (-item[1], item[0]))
        return topics

    def get_personality_summary(self):
        if self.assistant_mode:
            return "Assistant mode enabled. Personality styling is disabled."
        bond_level = self.memory.recall("personality", "bond_level") or "0.00"
        interaction_count = self.memory.recall("personality", "interaction_count") or "0"
        last_topic = self.memory.recall("personality", "last_topic") or "general"
        learned_topics = self._learned_topics()
        likes = self.memory.recall("facts", "likes")
        lines = [
            f"Bond level: {bond_level}",
            f"Interaction count: {interaction_count}",
            f"Last topic: {last_topic}",
        ]
        if likes:
            lines.append(f"User likes: {likes}")
        if learned_topics:
            top_topics = ", ".join(f"{topic} ({count})" for topic, count in learned_topics[:5])
            lines.append(f"Learned interests: {top_topics}")
        return "\n".join(lines)

    def get_context(self, limit=8, query: str | None = None):
        """Return conversation history and extracted facts as a string."""
        recent_history = self.get_recent_history(limit=limit)
        context = "\n".join([f"{msg['role']}: {msg['text']}" for msg in recent_history])
        # Retrieve facts from memory
        name = self.memory.recall("facts", "name")
        likes = self.memory.recall("facts", "likes")
        relevant = []
        if query:
            try:
                relevant = self.memory.search(query, limit=5)
            except Exception:
                relevant = []
        facts_str = ""
        if name:
            facts_str += f"User's name: {name}\n"
        if likes:
            facts_str += f"User likes: {likes}\n"
        if relevant:
            facts_str += "Relevant memory:\n"
            for item in relevant[:5]:
                if item.get("source") == "kv":
                    facts_str += f"- {item.get('key')}: {item.get('value')}\n"
                elif item.get("source") == "memory":
                    facts_str += f"- {item.get('category')}:{item.get('key')} = {item.get('value')}\n"
                elif item.get("source") == "event":
                    facts_str += f"- {item.get('event_type')}: {item.get('data')}\n"
        if self.assistant_mode:
            return f"Conversation history:\n{context}\n\nStored facts:\n{facts_str}"
        interaction_count = self.memory.recall("personality", "interaction_count")
        bond_level = self.memory.recall("personality", "bond_level")
        if interaction_count:
            facts_str += f"Interaction count: {interaction_count}\n"
        if bond_level:
            facts_str += f"Bond level: {bond_level}\n"
        personality_profile = self.get_personality_summary()
        return f"Conversation history:\n{context}\n\nStored facts:\n{facts_str}\n\nPersonality profile:\n{personality_profile}"
