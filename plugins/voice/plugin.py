import logging
import threading

from .wake import WakeWordDetector
from .stt import SpeechToText
from .tts import TextToSpeech

logger = logging.getLogger(__name__)


def start(bus, hal, memory, config):
    """Start voice plugin: wake word, STT, TTS."""
    voice_config = config.get("voice", {})
    wake_enabled = voice_config.get("enabled", True)
    if not hasattr(bus, "_mic_lock"):
        bus._mic_lock = threading.Lock()
    if not hasattr(bus, "_voice_followup_active"):
        bus._voice_followup_active = False
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

    logger.info(
        "Voice: ready (wake=%s, stt=%s, tts=%s)",
        voice_config.get("wake_mode", "whisper"),
        voice_config.get("whisper_model", "tiny"),
        "piper" if voice_config.get("tts_model") else "system",
    )
