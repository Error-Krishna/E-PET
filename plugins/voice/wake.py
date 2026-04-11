import logging
import os
import tempfile
import threading
import time
import wave
import numpy as np

logger = logging.getLogger(__name__)

try:
    import pvporcupine

    PORCUPINE_AVAILABLE = True
except ImportError:
    PORCUPINE_AVAILABLE = False
    logger.debug("pvporcupine not installed")

try:
    from faster_whisper import WhisperModel
    FASTER_WHISPER_AVAILABLE = True
except ImportError:
    FASTER_WHISPER_AVAILABLE = False
    WhisperModel = None

try:
    from pvrecorder import PvRecorder
    PVRECORDER_AVAILABLE = True
except ImportError:
    PVRECORDER_AVAILABLE = False
    PvRecorder = None

try:
    import pyaudio

    PYAUDIO_AVAILABLE = True
except ImportError:
    PYAUDIO_AVAILABLE = False
    pyaudio = None

WHISPER_WAKE_AVAILABLE = FASTER_WHISPER_AVAILABLE and (PVRECORDER_AVAILABLE or PYAUDIO_AVAILABLE)


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
        self.listen_seconds = max(
            1,
            int(
                self.voice_config.get(
                    "follow_up_listen_seconds",
                    self.voice_config.get("wake_listen_seconds", 2),
                )
            ),
        )
        self.check_interval = float(self.voice_config.get("wake_check_interval", 0.3))
        self.cooldown_seconds = float(self.voice_config.get("wake_cooldown_seconds", 4.0))
        self.mic_lock_timeout = max(0.5, float(self.voice_config.get("mic_lock_timeout", 5.0)))
        self.porcupine = None
        self.whisper_model = None
        self.recorder = None
        self.audio_interface = None
        self.audio_stream = None
        self._mode = "keyboard"
        self._last_wake_time = 0.0
        self._read_error_count = 0
        self._mic_lock = getattr(bus, "_mic_lock", None)
        self._paused = threading.Event()
        self._configure_detector()

    def _configure_detector(self):
        if self.wake_mode in {"auto", "porcupine"}:
            if self._setup_porcupine():
                return

        if self.wake_mode in {"auto", "whisper"}:
            if self._setup_whisper_wake():
                return

        logger.info("Wake: keyboard fallback (press space)")

    def _setup_porcupine(self):
        if not PORCUPINE_AVAILABLE:
            return False
        try:
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
            self._init_recorder(frame_length=self.porcupine.frame_length)
            self._mode = "porcupine"
            logger.info(f"Wake: Porcupine '{self.wake_keyword}'")
            return True
        except Exception as e:
            logger.debug(f"Porcupine initialisation failed: {e}")
            self.porcupine = None
            self.audio_stream = None
            self.recorder = None
            return False

    def _setup_whisper_wake(self):
        if not WHISPER_WAKE_AVAILABLE:
            return False
        try:
            self._init_recorder(frame_length=1024)
            self._mode = "whisper"
            logger.info(f"Wake: Whisper phrase '{self.wake_word}' ({self.wake_whisper_model})")
            return True
        except Exception as e:
            logger.debug(f"Whisper wake detector initialisation failed: {e}")
            self.audio_interface = None
            self.recorder = None
            return False

    def _init_recorder(self, frame_length):
        if PvRecorder is not None:
            self.recorder = PvRecorder(device_index=-1, frame_length=frame_length)
            self.audio_stream = self.recorder
            self.audio_interface = self.recorder
            return

        import pyaudio

        self.audio_interface = pyaudio.PyAudio()
        self.audio_stream = self.audio_interface.open(
            rate=16000,
            channels=1,
            format=pyaudio.paInt16,
            input=True,
            frames_per_buffer=frame_length,
        )

    def start(self):
        self._thread = threading.Thread(target=self._run)
        self._thread.daemon = True
        self._thread.start()
        self.bus.subscribe("pet/voice/tts_state", self._on_tts_state)

    def stop(self):
        self._running = False
        self._close_audio_resources()
        if self._thread:
            self._thread.join(timeout=1)
        self._thread = None
        self.porcupine = None
        self.whisper_model = None

    def pause_detection(self):
        self._paused.set()

    def resume_detection(self):
        self._paused.clear()

    def _on_tts_state(self, topic, data):
        if not self._running:
            return
        state = str((data or {}).get("state", "")).strip().lower()
        if state == "speaking":
            self.pause_detection()
        elif state in {"idle", "stopped", "error"}:
            if getattr(self.bus, "_voice_followup_active", False):
                self.pause_detection()
                return
            self.resume_detection()

    def _close_audio_resources(self):
        if self.audio_stream and hasattr(self.audio_stream, "stop_stream"):
            try:
                self.audio_stream.stop_stream()
            except Exception:
                pass
        if self.audio_stream and hasattr(self.audio_stream, "close"):
            try:
                self.audio_stream.close()
            except Exception:
                pass
        if self.recorder and hasattr(self.recorder, "stop"):
            try:
                self.recorder.stop()
            except Exception:
                pass
        if self.recorder and hasattr(self.recorder, "delete"):
            try:
                self.recorder.delete()
            except Exception:
                pass
        if self.audio_interface and hasattr(self.audio_interface, "terminate"):
            try:
                self.audio_interface.terminate()
            except Exception:
                pass
        if self.porcupine and hasattr(self.porcupine, "delete"):
            try:
                self.porcupine.delete()
            except Exception:
                pass
        self.audio_stream = None
        self.recorder = None
        self.audio_interface = None
        self.porcupine = None

    def _run(self):
        if self._mode == "porcupine" and self.porcupine is not None and self.audio_stream is not None:
            # Real mic detection
            while self._running:
                try:
                    if self._paused.is_set():
                        time.sleep(0.05)
                        continue
                    pcm = self._read_porcupine_frame()
                    if pcm:
                        keyword_index = self.porcupine.process(pcm)
                        if keyword_index >= 0:
                            logger.info("Wake: triggered")
                            self.bus.publish(
                                "pet/input/wake_word",
                                {"source": "mic", "wake_word": self.wake_word},
                            )
                    self._read_error_count = 0
                except Exception as e:
                    if not self._running:
                        break
                    self._read_error_count += 1
                    logger.error(f"Error in audio loop: {e}")
                    if self._read_error_count >= 2:
                        logger.warning("Wake: disabling mic wake after repeated device read failures")
                        self._running = False
                        break
                time.sleep(0.01)
        elif self._mode == "whisper":
            while self._running:
                try:
                    if self._paused.is_set():
                        time.sleep(0.05)
                        continue
                    if time.time() - self._last_wake_time < self.cooldown_seconds:
                        time.sleep(0.1)
                        continue
                    transcript = self._capture_and_transcribe_phrase()
                    if transcript and self._contains_wake_phrase(transcript):
                        self._publish_wake("mic", transcript)
                    self._read_error_count = 0
                except Exception as e:
                    if not self._running:
                        break
                    self._read_error_count += 1
                    logger.error(f"Error in spoken wake loop: {e}")
                    if self._read_error_count >= 2:
                        logger.warning("Wake: disabling spoken mic wake after repeated device read failures")
                        self._running = False
                        break
                time.sleep(self.check_interval)
        else:
            while self._running:
                time.sleep(0.1)

    def _ensure_whisper_model_loaded(self):
        if self.whisper_model is None:
            logger.info(f"Wake: loading Whisper model '{self.wake_whisper_model}'")
            self.whisper_model = WhisperModel(self.wake_whisper_model, device="cpu", compute_type="int8")
            logger.info("Wake: model ready")

    def _begin_mic_capture(self):
        if self._mic_lock is None:
            return True
        deadline = time.time() + self.mic_lock_timeout
        while self._running:
            try:
                if self._mic_lock.acquire(timeout=0.1):
                    return True
            except Exception:
                break
            if time.time() >= deadline:
                return False
        return False

    def _end_mic_capture(self):
        if self._mic_lock is None:
            return
        try:
            self._mic_lock.release()
        except Exception:
            pass

    def _read_porcupine_frame(self):
        if self._mic_lock is None:
            return self._read_porcupine_frame_unlocked()
        if not self._begin_mic_capture():
            return None
        try:
            return self._read_porcupine_frame_unlocked()
        finally:
            self._end_mic_capture()

    def _read_porcupine_frame_unlocked(self):
        if self.recorder is not None:
            return self.audio_stream.read()
        return self.audio_stream.read(
            self.porcupine.frame_length,
            exception_on_overflow=False,
        )

    def _capture_and_transcribe_phrase(self):
        self._ensure_whisper_model_loaded()
        rate = 16000
        frames = []
        if not self._begin_mic_capture():
            return ""
        try:
            if self.recorder is not None:
                frame_length = getattr(self.recorder, "frame_length", 1024)
                self.recorder.start()
                try:
                    for _ in range(0, int(rate / frame_length * self.listen_seconds)):
                        frames.extend(self.audio_stream.read())
                finally:
                    try:
                        self.recorder.stop()
                    except Exception:
                        pass
            else:
                if pyaudio is None:
                    raise RuntimeError("pyaudio backend is not available")
                import pyaudio

                chunk = 1024
                stream = self.audio_interface.open(
                    format=pyaudio.paInt16,
                    channels=1,
                    rate=rate,
                    input=True,
                    frames_per_buffer=chunk,
                )
                try:
                    for _ in range(0, int(rate / chunk * self.listen_seconds)):
                        frames.append(stream.read(chunk, exception_on_overflow=False))
                finally:
                    try:
                        stream.stop_stream()
                    except Exception:
                        pass
                    try:
                        stream.close()
                    except Exception:
                        pass
        finally:
            self._end_mic_capture()

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
            path = handle.name
        try:
            with wave.open(path, "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setframerate(rate)
                if self.recorder is not None:
                    wav_file.setsampwidth(2)
                    wav_file.writeframes(np.asarray(frames, dtype=np.int16).tobytes())
                else:
                    if pyaudio is None:
                        raise RuntimeError("pyaudio backend is not available")
                    import pyaudio

                    wav_file.setsampwidth(self.audio_interface.get_sample_size(pyaudio.paInt16))
                    wav_file.writeframes(b"".join(frames))
            segments, _ = self.whisper_model.transcribe(path, beam_size=1)
            return " ".join([segment.text for segment in segments]).strip()
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def _contains_wake_phrase(self, transcript):
        cleaned_transcript = str(transcript).strip().lower()
        cleaned_wake = str(self.wake_word).strip().lower()
        return bool(cleaned_transcript and cleaned_wake and cleaned_wake in cleaned_transcript)

    def _publish_wake(self, source, transcript=""):
        self._last_wake_time = time.time()
        logger.info(f"Wake: triggered ({source})")
        self.bus.publish(
            "pet/input/wake_word",
            {"source": source, "wake_word": self.wake_word, "transcript": transcript},
        )
