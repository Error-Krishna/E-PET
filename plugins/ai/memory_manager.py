import json
import logging
import re
import time

logger = logging.getLogger(__name__)


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

    def start(self):
        self._load_persisted_history()
        self.bus.subscribe("pet/input/speech", self._on_speech)
        self.bus.subscribe("pet/ai/response", self._on_response)
        self.bus.subscribe("pet/ai/action", self._on_action)
        logger.info("Memory manager started")

    def stop(self):
        self._running = False

    def _on_speech(self, topic, data):
        text = data.get("text", "")
        self._add_to_history("user", text)
        self._extract_and_store_facts(text)
        self._increment_interaction_count()

    def _on_response(self, topic, data):
        text = data.get("text", "")
        self._add_to_history("assistant", text)

    def _on_action(self, topic, data):
        if data.get("type") == "remember_fact":
            key = data.get("key", "").strip()
            value = data.get("value", "").strip()
            if key and value:
                self.memory.remember("facts", key, value)

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

    def _extract_and_store_facts(self, text):
        if not self.extract_facts:
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

    def get_context(self):
        """Return conversation history and extracted facts as a string."""
        # Simple: join last N messages
        context = "\n".join(
            [f"{msg['role']}: {msg['text']}" for msg in self._history[-self.max_history :]]
        )
        # Retrieve facts from memory
        name = self.memory.recall("facts", "name")
        likes = self.memory.recall("facts", "likes")
        interaction_count = self.memory.recall("personality", "interaction_count")
        bond_level = self.memory.recall("personality", "bond_level")
        facts_str = ""
        if name:
            facts_str += f"User's name: {name}\n"
        if likes:
            facts_str += f"User likes: {likes}\n"
        if interaction_count:
            facts_str += f"Interaction count: {interaction_count}\n"
        if bond_level:
            facts_str += f"Bond level: {bond_level}\n"
        return f"Conversation history:\n{context}\n\nStored facts:\n{facts_str}"
