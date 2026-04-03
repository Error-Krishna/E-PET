import logging
import os
import tempfile
import threading
import time
import wave

logger = logging.getLogger(__name__)

# Try to import porcupine; fallback to keyboard only
try:
    import pvporcupine

    PORCUPINE_AVAILABLE = True
except ImportError:
    PORCUPINE_AVAILABLE = False
    logger.warning("pvporcupine not installed; wake word disabled, use SPACE key as fallback")

try:
    import whisper
    import pyaudio

    WHISPER_WAKE_AVAILABLE = True
except ImportError:
    WHISPER_WAKE_AVAILABLE = False


class WakeWordDetector:
    BUILTIN_KEYWORDS = {
        "computer",
        "jarvis",
        "bumblebee",
        "porcupine",
        "picovoice",
        "alexa",
        "americano",
        "blueberry",
        "grapefruit",
        "grasshopper",
        "hey google",
        "hey siri",
    }

    def __init__(self, bus, hal, memory, config):
        self.bus = bus
        self.hal = hal
        self.memory = memory
        self.config = config
        self._running = True
        self._thread = None
        self.voice_config = config.get("voice", {})
        self.wake_word = self.voice_config.get("wake_word", "hey pip")
        self.wake_keyword = self.voice_config.get("wake_keyword", "computer")
        self.access_key = self.voice_config.get("porcupine_access_key", "")
        self.keyword_path = self.voice_config.get("porcupine_keyword_path", "")
        self.wake_mode = self.voice_config.get("wake_mode", "auto")
        self.wake_whisper_model = self.voice_config.get(
            "wake_whisper_model",
            self.voice_config.get("whisper_model", "tiny"),
        )
        self.listen_seconds = max(1, int(self.voice_config.get("wake_listen_seconds", 2)))
        self.check_interval = float(self.voice_config.get("wake_check_interval", 0.3))
        self.cooldown_seconds = float(self.voice_config.get("wake_cooldown_seconds", 4.0))
        self.porcupine = None
        self.whisper_model = None
        self.audio_interface = None
        self.audio_stream = None
        self._mode = "keyboard"
        self._last_wake_time = 0.0
        self._configure_detector()

    def _configure_detector(self):
        if self.wake_mode in {"auto", "porcupine"}:
            if self._setup_porcupine():
                return

        if self.wake_mode in {"auto", "whisper"}:
            if self._setup_whisper_wake():
                return

        logger.info("Wake word detector using keyboard fallback via input simulator (press SPACE)")

    def _setup_porcupine(self):
        if not PORCUPINE_AVAILABLE:
            return False
        try:
            import pyaudio

            if not self.access_key:
                raise ValueError("missing porcupine_access_key in config.voice")
            create_kwargs = {"access_key": self.access_key}
            if self.keyword_path:
                create_kwargs["keyword_paths"] = [self.keyword_path]
            else:
                keyword = str(self.wake_keyword).strip().lower()
                if keyword not in self.BUILTIN_KEYWORDS:
                    raise ValueError(
                        f"wake_keyword '{self.wake_keyword}' is not a Porcupine built-in keyword "
                        "and no porcupine_keyword_path was provided"
                    )
                create_kwargs["keywords"] = [keyword]

            self.porcupine = pvporcupine.create(**create_kwargs)
            self.audio_interface = pyaudio.PyAudio()
            self.audio_stream = self.audio_interface.open(
                rate=self.porcupine.sample_rate,
                channels=1,
                format=pyaudio.paInt16,
                input=True,
                frames_per_buffer=self.porcupine.frame_length,
            )
            self._mode = "porcupine"
            logger.info(f"Wake word detector initialised for '{self.wake_keyword}'")
            return True
        except Exception as e:
            logger.warning(f"Porcupine initialisation failed: {e}")
            self.porcupine = None
            self.audio_stream = None
            return False

    def _setup_whisper_wake(self):
        if not WHISPER_WAKE_AVAILABLE:
            return False
        try:
            import pyaudio

            self.audio_interface = pyaudio.PyAudio()
            self._mode = "whisper"
            logger.info(
                f"Wake word detector using spoken wake phrase '{self.wake_word}' "
                f"with Whisper model '{self.wake_whisper_model}'"
            )
            return True
        except Exception as e:
            logger.warning(f"Whisper wake detector initialisation failed: {e}")
            self.audio_interface = None
            return False

    def start(self):
        self._thread = threading.Thread(target=self._run)
        self._thread.daemon = True
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=1)
        if self.audio_stream and hasattr(self.audio_stream, "stop_stream"):
            self.audio_stream.stop_stream()
        if self.audio_stream and hasattr(self.audio_stream, "close"):
            self.audio_stream.close()
        if self.audio_interface and hasattr(self.audio_interface, "terminate"):
            self.audio_interface.terminate()
        if self.porcupine and hasattr(self.porcupine, "delete"):
            self.porcupine.delete()

    def _run(self):
        if self._mode == "porcupine" and self.porcupine is not None and self.audio_stream is not None:
            # Real mic detection
            while self._running:
                try:
                    pcm = self.audio_stream.read(
                        self.porcupine.frame_length,
                        exception_on_overflow=False,
                    )
                    if pcm:
                        keyword_index = self.porcupine.process(pcm)
                        if keyword_index >= 0:
                            logger.info("Wake word detected")
                            self.bus.publish(
                                "pet/input/wake_word",
                                {"source": "mic", "wake_word": self.wake_word},
                            )
                except Exception as e:
                    logger.error(f"Error in audio loop: {e}")
                time.sleep(0.01)
        elif self._mode == "whisper":
            while self._running:
                try:
                    if time.time() - self._last_wake_time < self.cooldown_seconds:
                        time.sleep(0.1)
                        continue
                    transcript = self._capture_and_transcribe_phrase()
                    if transcript and self._contains_wake_phrase(transcript):
                        self._publish_wake("mic", transcript)
                except Exception as e:
                    logger.error(f"Error in spoken wake loop: {e}")
                time.sleep(self.check_interval)
        else:
            while self._running:
                time.sleep(0.1)

    def _ensure_whisper_model_loaded(self):
        if self.whisper_model is None:
            logger.info(f"Loading wake-word Whisper model '{self.wake_whisper_model}'...")
            self.whisper_model = whisper.load_model(self.wake_whisper_model)
            logger.info("Wake-word Whisper model loaded")

    def _capture_and_transcribe_phrase(self):
        import pyaudio

        self._ensure_whisper_model_loaded()
        chunk = 1024
        rate = 16000
        stream = self.audio_interface.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=rate,
            input=True,
            frames_per_buffer=chunk,
        )
        frames = []
        try:
            for _ in range(0, int(rate / chunk * self.listen_seconds)):
                frames.append(stream.read(chunk, exception_on_overflow=False))
        finally:
            stream.stop_stream()
            stream.close()

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
            path = handle.name
        try:
            with wave.open(path, "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(self.audio_interface.get_sample_size(pyaudio.paInt16))
                wav_file.setframerate(rate)
                wav_file.writeframes(b"".join(frames))
            result = self.whisper_model.transcribe(path, fp16=False)
            return result.get("text", "").strip()
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def _contains_wake_phrase(self, transcript):
        cleaned_transcript = str(transcript).strip().lower()
        cleaned_wake = str(self.wake_word).strip().lower()
        return bool(cleaned_transcript and cleaned_wake and cleaned_wake in cleaned_transcript)

    def _publish_wake(self, source, transcript=""):
        self._last_wake_time = time.time()
        logger.info(f"Wake word detected from {source}")
        self.bus.publish(
            "pet/input/wake_word",
            {"source": source, "wake_word": self.wake_word, "transcript": transcript},
        )
