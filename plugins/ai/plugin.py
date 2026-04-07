import logging
import queue
import threading

from .memory_manager import MemoryManager
from .brain import AIBrain

logger = logging.getLogger(__name__)


def start(bus, hal, memory, config):
    """Start AI plugin: memory manager and brain."""
    ai_config = config.get("ai", {})
    enabled = ai_config.get("enabled", True)
    if not enabled:
        logger.debug("AI plugin disabled")
        return

    memory_manager = MemoryManager(bus, hal, memory, config)
    memory_manager.start()
    bus._memory_manager = memory_manager

    bus._ai_queue = queue.Queue(maxsize=1)

    brain = AIBrain(bus, hal, memory, config)
    brain.start()
    bus._brain = brain

    def _ai_worker():
        while True:
            payload = bus._ai_queue.get()
            if payload is None:
                break
            brain._process_ai_request(payload)

    ai_worker = threading.Thread(target=_ai_worker, daemon=True)
    ai_worker.start()
    bus._ai_worker = ai_worker

    logger.info("AI: ready")
