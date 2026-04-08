from __future__ import annotations

import logging
import queue
from typing import Any

from core.utils import unwrap_event_payload

logger = logging.getLogger(__name__)


class ConversationBridge:
    def __init__(self, event_queue):
        self.event_queue = event_queue

    def bind(self, bus):
        bus.subscribe("pet/input/speech", self._on_input)
        bus.subscribe("pet/voice/transcript", self._on_input)
        bus.subscribe("pet/ai/response", self._on_response)
        bus.subscribe("pet/system/quit", self._on_quit)
        logger.info("Runtime | conversation bridge ready")

    def _enqueue(self, kind: str, data: Any):
        payload = {"kind": kind, "data": data}
        try:
            if self.event_queue.full():
                self.event_queue.get_nowait()
            self.event_queue.put_nowait(payload)
        except queue.Empty:
            self.event_queue.put_nowait(payload)
        except queue.Full:
            pass

    def _on_input(self, topic, data):
        data = unwrap_event_payload(data)
        self._enqueue("input", data)

    def _on_response(self, topic, data):
        data = unwrap_event_payload(data)
        self._enqueue("response", data)

    def _on_quit(self, topic, data):
        data = unwrap_event_payload(data)
        self._enqueue("quit", data)
