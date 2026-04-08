import logging

from .executor import OSBridgeExecutor

logger = logging.getLogger(__name__)


def start(bus, hal, memory, config):
    os_config = config.get("os_bridge", {})
    if not os_config.get("enabled", True):
        logger.info("OS bridge disabled")
        return

    executor = OSBridgeExecutor(bus, hal, memory, config)
    executor.start()
    bus._os_bridge = executor
    logger.info("OS bridge: ready")
