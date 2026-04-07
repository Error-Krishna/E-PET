import threading
import time
import logging

logger = logging.getLogger(__name__)

class IdleTick:
    """Background thread that emits a tick event every second."""
    def __init__(self, bus, config):
        self.bus = bus
        self.config = config
        self._running = True
        self._thread = None
        self._tick_count = 0

    def start(self):
        self._thread = threading.Thread(target=self._run)
        self._thread.daemon = True
        self._thread.start()
        logger.info("Idle: ticking every 1s")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=1)

    def _run(self):
        while self._running:
            self._tick_count += 1
            self.bus.publish("pet/system/tick", {
                "timestamp": time.time(),
                "tick_count": self._tick_count,
            })
            time.sleep(1)

def start(bus, hal, memory, config):
    engine = IdleTick(bus, config)
    engine.start()
    bus._idle_tick = engine
    return engine
