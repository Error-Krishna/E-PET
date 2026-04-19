import logging
import os
import re
import tempfile
import threading
import time
import wave
from collections import deque
from difflib import SequenceMatcher
import numpy as np

from core.utils import unwrap_event_payload

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
        self.wake_max_phrase_seconds = max(
            float(self.listen_seconds),
            float(self.voice_config.get("wake_max_phrase_seconds", max(2.5, self.listen_seconds))),
        )
        self.wake_silence_seconds = max(
            0.2,
            float(self.voice_config.get("wake_silence_seconds", 0.55)),
        )
        self.wake_min_speech_seconds = max(
            0.1,
            float(self.voice_config.get("wake_min_speech_seconds", 0.2)),
        )
        self.wake_vad_sensitivity = min(
            1.0,
            max(0.0, float(self.voice_config.get("wake_vad_sensitivity", 0.68))),
        )
        self.wake_language = str(
            self.voice_config.get("wake_language", self.voice_config.get("stt_language", "en"))
        ).strip().lower() or "auto"
        self.wake_initial_prompt = str(
            self.voice_config.get("wake_initial_prompt", f"wake word is {self.wake_word}")
        ).strip()
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
        self._whisper_model_lock = threading.Lock()
        self._recorder_lock = threading.Lock()
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
            self._mode = "whisper"
            self._init_recorder(frame_length=1024)
            logger.info(f"Wake: Whisper phrase '{self.wake_word}' ({self.wake_whisper_model})")
            return True
        except Exception as e:
            logger.debug(f"Whisper wake detector initialisation failed: {e}")
            self.audio_interface = None
            self.recorder = None
            return False

    def _init_recorder(self, frame_length):
        with self._recorder_lock:
            if self.recorder is not None or self.audio_stream is not None:
                return
            if PvRecorder is not None:
                self.recorder = PvRecorder(device_index=-1, frame_length=frame_length)
                self.audio_stream = self.recorder
                self.audio_interface = self.recorder
                if hasattr(self.recorder, "start"):
                    try:
                        self.recorder.start()
                    except Exception:
                        pass
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
            self._thread.join(timeout=self.listen_seconds + 1)
        self._thread = None
        self.porcupine = None
        self.whisper_model = None

    def pause_detection(self):
        self._paused.set()

    def resume_detection(self):
        self._paused.clear()

    def _on_tts_state(self, topic, data):
        data = unwrap_event_payload(data)
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
        with self._recorder_lock:
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
                    # Cooldown prevents the wake loop from re-triggering on the
                    # pet's own response audio after follow-up listening ends.
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
        if self.whisper_model is not None:
            return
        with self._whisper_model_lock:
            if self.whisper_model is None:
                logger.info(f"Wake: loading Whisper model '{self.wake_whisper_model}'")
                try:
                    self.whisper_model = WhisperModel(self.wake_whisper_model, device="cpu", compute_type="int8")
                except Exception as exc:
                    self.whisper_model = None
                    raise RuntimeError(
                        f"Whisper model failed to load or download: {exc}. Check your internet connection and model path."
                    ) from exc
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
        sample_width = 2
        if not self._begin_mic_capture():
            return ""
        try:
            if self.recorder is None and self.audio_stream is None:
                try:
                    self._init_recorder(frame_length=1024)
                except Exception as exc:
                    logger.debug(f"Wake: deferred recorder init failed: {exc}")
                    return ""
            capture = self._capture_wake_audio_window()
        finally:
            self._end_mic_capture()

        if capture is None or capture.size == 0:
            return ""

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
            path = handle.name
        try:
            with wave.open(path, "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setframerate(rate)
                wav_file.setsampwidth(sample_width)
                wav_file.writeframes(capture.astype(np.int16).tobytes())
            transcribe_kwargs = {
                "beam_size": 1,
                "condition_on_previous_text": False,
                "vad_filter": True,
                "word_timestamps": False,
                "temperature": 0.0,
            }
            if self.wake_language != "auto":
                transcribe_kwargs["language"] = self.wake_language
            if self.wake_initial_prompt:
                transcribe_kwargs["initial_prompt"] = self.wake_initial_prompt
            segments, _ = self.whisper_model.transcribe(path, **transcribe_kwargs)
            return " ".join([segment.text for segment in segments]).strip()
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def _capture_wake_audio_window(self):
        if self.recorder is not None:
            frame_size = max(256, int(getattr(self.recorder, "frame_length", 1024)))
            reader = lambda: self.audio_stream.read()
        else:
            if pyaudio is None:
                return np.asarray([], dtype=np.int16)
            chunk = 1024
            if self.audio_stream is None:
                self.audio_stream = self.audio_interface.open(
                    format=pyaudio.paInt16,
                    channels=1,
                    rate=16000,
                    input=True,
                    frames_per_buffer=chunk,
                )
            frame_size = chunk
            reader = lambda: self.audio_stream.read(chunk, exception_on_overflow=False)

        pre_roll_frames = max(1, int(round(0.3 * 16000 / frame_size)))
        min_speech_frames = max(1, int(round(self.wake_min_speech_seconds * 16000 / frame_size)))
        silence_frames_required = max(1, int(round(self.wake_silence_seconds * 16000 / frame_size)))
        start_timeout_frames = max(1, int(round(float(self.listen_seconds) * 16000 / frame_size)))
        max_phrase_frames = max(1, int(round(self.wake_max_phrase_seconds * 16000 / frame_size)))
        noise_baseline = deque(maxlen=max(4, int(round(0.45 * 16000 / frame_size))))
        pre_roll = deque(maxlen=pre_roll_frames)

        frames = []
        speech_started = False
        speech_frames = 0
        silence_count = 0
        start_votes = 0
        waited_frames = 0

        while self._running and waited_frames < max_phrase_frames:
            waited_frames += 1
            try:
                raw = reader()
            except Exception as exc:
                logger.debug("Wake: audio read failed during whisper capture: %s", exc)
                return np.asarray([], dtype=np.int16)

            pcm = self._to_pcm_frame(raw)
            if pcm.size == 0:
                continue
            energy = self._frame_energy(pcm)
            threshold = self._wake_dynamic_threshold(noise_baseline)

            if not speech_started:
                noise_baseline.append(energy)
                pre_roll.append(pcm)
                if energy >= threshold:
                    start_votes += 1
                else:
                    start_votes = 0
                if start_votes >= (1 if self.wake_vad_sensitivity >= 0.8 else 2):
                    speech_started = True
                    frames.extend(list(pre_roll))
                    speech_frames = 0
                    silence_count = 0
                elif waited_frames >= start_timeout_frames:
                    return np.asarray([], dtype=np.int16)
                continue

            frames.append(pcm)
            speech_frames += 1
            if energy >= threshold:
                silence_count = 0
            else:
                silence_count += 1
                if silence_count >= silence_frames_required and speech_frames >= min_speech_frames:
                    break

        if not speech_started or speech_frames < min_speech_frames:
            return np.asarray([], dtype=np.int16)
        return self._preprocess_wake_pcm(np.concatenate(frames).astype(np.int16))

    @staticmethod
    def _to_pcm_frame(raw):
        if isinstance(raw, bytes):
            return np.frombuffer(raw, dtype=np.int16)
        return np.asarray(raw, dtype=np.int16).reshape(-1)

    def _wake_dynamic_threshold(self, baseline):
        if baseline:
            ambient = float(np.median(np.asarray(list(baseline), dtype=np.float32)))
        else:
            ambient = 70.0
        floor = max(70.0, 170.0 - 90.0 * self.wake_vad_sensitivity)
        multiplier = max(1.04, 1.5 - 0.55 * self.wake_vad_sensitivity)
        ceiling = floor + 850.0
        return max(floor, min(ambient * multiplier, ceiling))

    @staticmethod
    def _frame_energy(frame):
        samples = np.asarray(frame, dtype=np.int16)
        if samples.size == 0:
            return 0.0
        return float(np.sqrt(np.mean(np.square(samples.astype(np.float32)))))

    @staticmethod
    def _preprocess_wake_pcm(samples):
        if samples.size == 0:
            return samples
        pcm = samples.astype(np.float32)
        pcm -= float(np.mean(pcm))
        rms = float(np.sqrt(np.mean(np.square(pcm))))
        if rms > 1.0:
            gain = min(3.0, 2600.0 / rms)
            pcm *= gain
        return np.clip(pcm, -32768.0, 32767.0).astype(np.int16)

    def _contains_wake_phrase(self, transcript):
        cleaned_transcript = self._normalize_phrase(transcript)
        cleaned_wake = self._normalize_phrase(self.wake_word)
        if not cleaned_transcript or not cleaned_wake:
            return False
        if cleaned_wake in cleaned_transcript:
            return True
        wake_tokens = cleaned_wake.split()
        transcript_tokens = cleaned_transcript.split()
        if not wake_tokens or not transcript_tokens:
            return False
        window = len(wake_tokens)
        if window == 1:
            wake_token = wake_tokens[0]
            return any(self._token_similarity(wake_token, token) >= 0.84 for token in transcript_tokens)
        if len(transcript_tokens) < window:
            return False
        for idx in range(0, len(transcript_tokens) - window + 1):
            candidate = transcript_tokens[idx : idx + window]
            similarities = [self._token_similarity(w, c) for w, c in zip(wake_tokens, candidate)]
            if similarities and (sum(similarities) / len(similarities)) >= 0.83:
                return True
        return False

    @staticmethod
    def _normalize_phrase(text):
        lowered = str(text or "").strip().lower()
        compact = re.sub(r"[^a-z0-9\s]+", " ", lowered)
        return " ".join(compact.split())

    @staticmethod
    def _token_similarity(left, right):
        return float(SequenceMatcher(a=str(left), b=str(right)).ratio())

    def _publish_wake(self, source, transcript=""):
        self._last_wake_time = time.time()
        logger.info(f"Wake: triggered ({source})")
        self.bus.publish(
            "pet/voice/state",
            {
                "state": "LISTENING",
                "source": source,
                "detail": "wake_word_detected",
                "timestamp": self._last_wake_time,
            },
        )
        self.bus.publish(
            "pet/input/wake_word",
            {"source": source, "wake_word": self.wake_word, "transcript": transcript},
        )
