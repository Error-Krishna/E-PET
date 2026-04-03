import logging

from .memory_manager import MemoryManager
from .brain import AIBrain

logger = logging.getLogger(__name__)


def start(bus, hal, memory, config):
    """Start AI plugin: memory manager and brain."""
    ai_config = config.get("ai", {})
    enabled = ai_config.get("enabled", True)
    if not enabled:
        logger.info("AI plugin disabled")
        return

    memory_manager = MemoryManager(bus, hal, memory, config)
    memory_manager.start()
    bus._memory_manager = memory_manager

    brain = AIBrain(bus, hal, memory, config)
    brain.start()
    bus._brain = brain

    logger.info("AI plugin loaded")
