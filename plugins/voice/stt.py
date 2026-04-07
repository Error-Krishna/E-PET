import logging
import threading
import time
import sys
import queue
import tempfile
import wave
import numpy as np

from core.platform_utils import is_interactive_input

logger = logging.getLogger(__name__)

# Try to import whisper and pyaudio; fallback to keyboard input
try:
    import whisper
    import pyaudio

    WHISPER_AVAILABLE = True
except ImportError:
    WHISPER_AVAILABLE = False
    logger.warning("Whisper or pyaudio not installed; STT will use keyboard fallback")


class SpeechToText:
    def __init__(self, bus, hal, memory, config):
        self.bus = bus
        self.hal = hal
        self.memory = memory
        self.config = config
        self._running = True
        self._thread = None
        self.model = None
        self.audio = None
        self.record_queue = queue.Queue(maxsize=1)
        self.voice_config = config.get("voice", {})
        self.model_name = self.voice_config.get("whisper_model", "tiny")
        if WHISPER_AVAILABLE:
            try:
                # Lazy-load Whisper on first wake event to improve startup time.
                self.audio = pyaudio.PyAudio()
                logger.info("STT audio interface ready")
            except Exception as e:
                logger.warning(f"Whisper initialisation failed: {e}")
                self.model = None
                self.audio = None
        else:
            logger.info("STT running in keyboard-fallback mode")

    def start(self):
        self._thread = threading.Thread(target=self._run)
        self._thread.daemon = True
        self._thread.start()
        self.bus.subscribe("pet/input/wake_word", self._on_wake)
        logger.info("STT started")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=1)
        if self.audio and hasattr(self.audio, "terminate"):
            self.audio.terminate()

    def _on_wake(self, topic, data):
        # Wake word triggers recording
        if self.model is not None and self.audio is not None:
            if self.record_queue.empty():
                self.record_queue.put_nowait(("record", None))
        elif WHISPER_AVAILABLE and self.audio is not None:
            if self.record_queue.empty():
                self.record_queue.put_nowait(("record", None))
        else:
            # Fallback: read from terminal input
            if not is_interactive_input():
                logger.info("STT fallback skipped because stdin is not interactive")
                return
            logger.info("STT fallback: enter text:")
            try:
                text = sys.stdin.readline().strip()
                if text:
                    self.bus.publish("pet/input/speech", {"text": text, "confidence": 1.0})
            except Exception as e:
                logger.error(f"Error in keyboard fallback: {e}")

    def _record_and_transcribe(self):
        self._ensure_model_loaded()
        # Record audio for a few seconds (adjustable)
        chunk = 1024
        format = pyaudio.paInt16
        channels = 1
        rate = 16000
        record_seconds = self.voice_config.get("record_seconds", 3)
        frames = []
        stream = self.audio.open(
            format=format,
            channels=channels,
            rate=rate,
            input=True,
            frames_per_buffer=chunk,
        )
        logger.debug("Recording started")
        for _ in range(0, int(rate / chunk * record_seconds)):
            data = stream.read(chunk)
            frames.append(data)
        logger.debug("Recording finished")
        stream.stop_stream()
        stream.close()
        # Save to temporary file
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            wf = wave.open(f.name, "wb")
            wf.setnchannels(channels)
            wf.setsampwidth(self.audio.get_sample_size(format))
            wf.setframerate(rate)
            wf.writeframes(b"".join(frames))
            wf.close()
            # Transcribe
            try:
                result = self.model.transcribe(f.name, fp16=False)
                text = result["text"].strip()
                if text:
                    logger.info(f"Transcribed: {text}")
                    self.bus.publish(
                        "pet/input/speech",
                        {"text": text, "confidence": result.get("confidence", 1.0)},
                    )
                else:
                    logger.info("No speech detected")
            except Exception as e:
                logger.error(f"Transcription error: {e}")

    def _ensure_model_loaded(self):
        if self.model is None:
            logger.info(f"Loading Whisper model '{self.model_name}'...")
            self.model = whisper.load_model(self.model_name)
            logger.info("Whisper model loaded")

    def _run(self):
        while self._running:
            try:
                action, _ = self.record_queue.get(timeout=0.1)
            except queue.Empty:
                continue

            if action == "record":
                try:
                    self._record_and_transcribe()
                except Exception as e:
                    logger.error(f"STT worker error: {e}")
