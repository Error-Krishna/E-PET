import threading
import logging
import fnmatch
import queue
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict, List, Any

logger = logging.getLogger(__name__)

class EventBus:
    """Simple event bus with topic-based pub/sub and wildcard support."""

    DOMAIN_MAP = {
        "voice": "pet/input/*",
        "ai": "pet/ai/*",
        "emotion": "pet/emotion/*",
        "system": "pet/system/*",
        "default": "*",
    }

    def __init__(self, config=None):
        self._subscribers: Dict[str, List[Callable]] = {}
        self._lock = threading.Lock()
        self._fast_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="epet-fast")
        self._slow_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="epet-slow")
        self._seq = 0
        self._seq_lock = threading.Lock()
        event_bus_config = (config or {}).get("event_bus", {})
        self._ordered = bool(event_bus_config.get("ordered", False))
        self._log_published_events = bool(event_bus_config.get("log_events", False))
        self._ordered_stop = threading.Event()
        self._ordered_queues: Dict[str, queue.Queue] = {}
        self._ordered_workers: Dict[str, threading.Thread] = {}
        if self._ordered:
            for domain in self.DOMAIN_MAP:
                domain_queue = queue.Queue()
                self._ordered_queues[domain] = domain_queue
                worker = threading.Thread(
                    target=self._ordered_worker,
                    args=(domain,),
                    daemon=True,
                    name=f"epet-bus-{domain}",
                )
                worker.start()
                self._ordered_workers[domain] = worker

    def subscribe(self, pattern: str, callback: Callable[[str, Any], None]) -> None:
        """Subscribe a callback to a topic pattern (supports * and ? wildcards)."""
        with self._lock:
            if pattern not in self._subscribers:
                self._subscribers[pattern] = []
            self._subscribers[pattern].append(callback)
        logger.debug(f"Subscribed callback to pattern: {pattern}")

    def publish(self, topic: str, data: Any) -> None:
        """Publish an event to all subscribers matching the topic."""
        if self._ordered:
            event = self._build_ordered_event(topic, data)
            if self._log_published_events:
                logger.info("[BUS] seq=%s topic=%s", event["seq"], topic)
            else:
                logger.debug("[BUS] seq=%s topic=%s", event["seq"], topic)
        else:
            logger.debug(f"Publishing event: topic={topic}, data={data}")
        with self._lock:
            patterns = list(self._subscribers.keys())
            if self._ordered:
                matching_callbacks = []
                for pattern in patterns:
                    if fnmatch.fnmatch(topic, pattern):
                        matching_callbacks.extend(self._subscribers[pattern][:])
            else:
                matching_patterns = [p for p in patterns if fnmatch.fnmatch(topic, p)]

        if self._ordered:
            if not matching_callbacks:
                logger.debug(f"No subscribers for topic {topic}")
                return
            domain = self._resolve_domain(topic)
            self._ordered_queues[domain].put(
                {
                    "event": event,
                    "callbacks": matching_callbacks,
                }
            )
            return

        if not matching_patterns:
            logger.debug(f"No subscribers for topic {topic}")
            return

        for pattern in matching_patterns:
            with self._lock:
                callbacks = self._subscribers[pattern][:]
            for callback in callbacks:
                pool = self._slow_pool if topic.startswith("pet/ai/") or topic.startswith("pet/voice/") else self._fast_pool
                pool.submit(self._run_callback, callback, topic, data)

    def _build_ordered_event(self, topic: str, data: Any) -> Dict[str, Any]:
        with self._seq_lock:
            self._seq += 1
            seq = self._seq

        timestamp = time.time()
        source = data.get("source") if isinstance(data, dict) else None
        if isinstance(data, dict):
            payload = dict(data)
            event = dict(payload)
            event["data"] = payload
        else:
            event = {"data": data}
        event.update(
            {
                "seq": seq,
                "timestamp": timestamp,
                "topic": topic,
                "source": source,
            }
        )
        return event

    def _resolve_domain(self, topic: str) -> str:
        for domain, pattern in self.DOMAIN_MAP.items():
            if fnmatch.fnmatch(topic, pattern):
                return domain
        return "default"

    def _ordered_worker(self, domain: str) -> None:
        domain_queue = self._ordered_queues[domain]
        while not self._ordered_stop.is_set():
            try:
                packet = domain_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if packet is None:
                break
            event = packet["event"]
            callbacks = packet["callbacks"]
            for callback in callbacks:
                self._run_callback(callback, event["topic"], event)

    def _run_callback(self, callback: Callable, topic: str, data: Any) -> None:
        """Run a callback, catching and logging any exception."""
        try:
            callback(topic, data)
        except Exception as e:
            logger.error(f"Error in callback for topic {topic}: {e}", exc_info=True)

    def shutdown(self) -> None:
        if self._ordered:
            self._ordered_stop.set()
            for domain_queue in self._ordered_queues.values():
                domain_queue.put(None)
            for worker in self._ordered_workers.values():
                worker.join(timeout=1)
        self._fast_pool.shutdown(wait=False)
        self._slow_pool.shutdown(wait=False)
