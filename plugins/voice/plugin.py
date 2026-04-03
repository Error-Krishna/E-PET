import logging

from .wake import WakeWordDetector
from .stt import SpeechToText
from .tts import TextToSpeech

logger = logging.getLogger(__name__)


def start(bus, hal, memory, config):
    """Start voice plugin: wake word, STT, TTS."""
    voice_config = config.get("voice", {})
    wake_enabled = voice_config.get("enabled", True)
    if wake_enabled:
        wake = WakeWordDetector(bus, hal, memory, config)
        wake.start()
        bus._wake = wake  # keep reference
        logger.info("Wake word detector started")
    else:
        logger.info("Wake word detector disabled")

    stt = SpeechToText(bus, hal, memory, config)
    stt.start()
    bus._stt = stt

    tts = TextToSpeech(bus, hal, memory, config)
    tts.start()
    bus._tts = tts

    logger.info("Voice plugin loaded")
