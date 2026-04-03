import threading
import logging
import fnmatch
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict, List, Any

logger = logging.getLogger(__name__)

class EventBus:
    """Simple event bus with topic-based pub/sub and wildcard support."""
    def __init__(self):
        self._subscribers: Dict[str, List[Callable]] = {}
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(
            max_workers=8,
            thread_name_prefix="epet-bus",
        )

    def subscribe(self, pattern: str, callback: Callable[[str, Any], None]) -> None:
        """Subscribe a callback to a topic pattern (supports * and ? wildcards)."""
        with self._lock:
            if pattern not in self._subscribers:
                self._subscribers[pattern] = []
            self._subscribers[pattern].append(callback)
        logger.debug(f"Subscribed callback to pattern: {pattern}")

    def publish(self, topic: str, data: Any) -> None:
        """Publish an event to all subscribers matching the topic."""
        logger.debug(f"Publishing event: topic={topic}, data={data}")
        # Find matching patterns
        with self._lock:
            patterns = list(self._subscribers.keys())
        matching_patterns = [p for p in patterns if fnmatch.fnmatch(topic, p)]

        if not matching_patterns:
            logger.debug(f"No subscribers for topic {topic}")
            return

        for pattern in matching_patterns:
            with self._lock:
                callbacks = self._subscribers[pattern][:]
            for callback in callbacks:
                # Use a bounded pool to avoid creating unbounded threads under load.
                self._executor.submit(self._run_callback, callback, topic, data)

    def _run_callback(self, callback: Callable, topic: str, data: Any) -> None:
        """Run a callback, catching and logging any exception."""
        try:
            callback(topic, data)
        except Exception as e:
            logger.error(f"Error in callback for topic {topic}: {e}", exc_info=True)
