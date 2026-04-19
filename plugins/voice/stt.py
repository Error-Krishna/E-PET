import logging
import os
import queue
import re
import sys
import tempfile
import threading
import time
import wave
from collections import deque
from dataclasses import dataclass
from difflib import SequenceMatcher
from inspect import signature

import numpy as np

from core.platform_utils import is_interactive_input
from core.utils import profile, unwrap_event_payload

logger = logging.getLogger(__name__)

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

WHISPER_AVAILABLE = FASTER_WHISPER_AVAILABLE and (PVRECORDER_AVAILABLE or PYAUDIO_AVAILABLE)


@dataclass
class _CaptureResult:
    frames: list
    backend: str


class SpeechToText:
    STATE_IDLE = "IDLE"
    STATE_LISTENING = "LISTENING"
    STATE_PROCESSING = "PROCESSING"
    STATE_RESPONDING = "RESPONDING"
    STATE_FOLLOW_UP_LISTENING = "FOLLOW_UP_LISTENING"

    def __init__(self, bus, hal, memory, config):
        self.bus = bus
        self.hal = hal
        self.memory = memory
        self.config = config
        self._running = True
        self._thread = None
        self.model = None
        self.audio = None
        self.recorder = None
        self._audio_lock = threading.RLock()
        self._state_lock = threading.RLock()
        self._request_lock = threading.Lock()
        self._voice_state = self.STATE_IDLE
        self._request_pending = False
        self._pending_follow_up_request = False
        self._tts_speaking = False
        self._last_tts_text = ""
        self._last_tts_finished_at = 0.0
        self._echo_retry_count = 0
        self._echo_retry_lock = threading.Lock()
        self._follow_up_retry_thread = None
        self._current_source = "wake_word"
        self._using_shared_audio = False
        self._model_prime_thread = None
        self.record_queue = queue.Queue(maxsize=1)
        self.voice_config = config.get("voice", {})
        self.model_name = self.voice_config.get("whisper_model", "tiny")
        self.stt_language = str(self.voice_config.get("stt_language", "en")).strip().lower() or "auto"
        self.stt_beam_size = max(1, int(self.voice_config.get("stt_beam_size", 5)))
        self.stt_best_of = max(1, int(self.voice_config.get("stt_best_of", 1)))
        self.stt_retry_beam_size = max(
            self.stt_beam_size,
            int(self.voice_config.get("stt_retry_beam_size", max(6, self.stt_beam_size))),
        )
        self.stt_retry_on_low_confidence = self._as_bool(
            self.voice_config.get("stt_retry_on_low_confidence", True)
        )
        self.stt_low_confidence_logprob = float(
            self.voice_config.get("stt_low_confidence_logprob", -1.05)
        )
        self.stt_no_speech_threshold = min(
            1.0,
            max(0.0, float(self.voice_config.get("stt_no_speech_threshold", 0.65))),
        )
        self.stt_apply_gain = self._as_bool(self.voice_config.get("stt_apply_gain", True))
        self.stt_target_rms = max(500.0, float(self.voice_config.get("stt_target_rms", 3800.0)))
        self.stt_max_gain = max(1.0, float(self.voice_config.get("stt_max_gain", 4.0)))
        self.stt_reject_non_latin_when_english = self._as_bool(
            self.voice_config.get("stt_reject_non_latin_when_english", True)
        )
        self.stt_min_latin_ratio = min(
            1.0,
            max(0.0, float(self.voice_config.get("stt_min_latin_ratio", 0.45))),
        )
        self.echo_guard_seconds = max(
            0.0,
            float(self.voice_config.get("echo_guard_seconds", 2.2)),
        )
        self.echo_similarity_threshold = min(
            1.0,
            max(0.5, float(self.voice_config.get("echo_similarity_threshold", 0.72))),
        )
        self.echo_min_chars = max(4, int(self.voice_config.get("echo_min_chars", 8)))
        self.echo_retry_limit = max(0, int(self.voice_config.get("echo_retry_limit", 2)))
        self.echo_retry_backoff_seconds = max(
            0.0,
            float(self.voice_config.get("echo_retry_backoff_seconds", 0.35)),
        )
        self._supports_kwarg_cache = {}
        self._mic_lock = getattr(bus, "_mic_lock", None)
        self._mic_lock_timeout = max(0.5, float(self.voice_config.get("mic_lock_timeout", 5.0)))
        self.sample_rate = 16000
        self.frame_length = max(256, int(self.voice_config.get("vad_frame_length", 1024)))
        self.pre_speech_seconds = max(0.15, float(self.voice_config.get("pre_speech_seconds", 0.4)))
        self.silence_threshold_seconds = max(
            0.5,
            float(self.voice_config.get("silence_threshold_seconds", 1.4)),
        )
        self.conversation_window_seconds = max(
            1.0,
            float(self.voice_config.get("conversation_window_seconds", self.voice_config.get("follow_up_listen_seconds", 8.0))),
        )
        self.min_speech_duration_seconds = max(
            0.1,
            float(self.voice_config.get("min_speech_duration_seconds", 0.35)),
        )
        self.vad_sensitivity = min(
            1.0,
            max(0.0, float(self.voice_config.get("vad_sensitivity", 0.65))),
        )
        self.speech_start_frames = 1 if self.vad_sensitivity >= 0.8 else 2
        self._min_energy_threshold = max(80.0, 220.0 - 100.0 * self.vad_sensitivity)
        self._noise_multiplier = max(1.05, 1.55 - 0.45 * self.vad_sensitivity)
        self._max_capture_seconds = max(20.0, self.conversation_window_seconds + self.silence_threshold_seconds + 12.0)
        if WHISPER_AVAILABLE:
            try:
                self._init_audio_backend()
            except Exception as e:
                logger.info(f"STT: falling back to keyboard input ({e})")
                self.model = None
                self.audio = None
                self.recorder = None
        else:
            logger.info("STT: keyboard fallback")

    @staticmethod
    def _as_bool(value):
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    def _init_audio_backend(self):
        if PvRecorder is not None:
            try:
                self.recorder = PvRecorder(device_index=-1, frame_length=self.frame_length)
                self.audio = self.recorder
                logger.info("STT: recorder ready")
                return
            except Exception as e:
                logger.debug(f"pvrecorder initialisation failed: {e}")
                self.recorder = None

        if pyaudio is None:
            raise RuntimeError("pyaudio backend is not available")
        self.audio = pyaudio.PyAudio()
        logger.info("STT: recorder ready")

    def start(self):
        self._thread = threading.Thread(target=self._run)
        self._thread.daemon = True
        self._thread.start()
        self.bus.subscribe("pet/input/wake_word", self._on_wake)
        self.bus.subscribe("pet/voice/listen_for_reply", self._on_listen_for_reply)
        self.bus.subscribe("pet/voice/tts_state", self._on_tts_state)
        if WHISPER_AVAILABLE:
            self._prime_model_async()
        logger.info("STT: ready")
        self._publish_state(self.STATE_IDLE, "startup")

    def stop(self):
        self._running = False
        self._close_audio_resources()
        setattr(self.bus, "_voice_followup_active", False)
        self._publish_state(self.STATE_IDLE, "shutdown")
        if self._thread:
            self._thread.join(timeout=1)
        self._thread = None
        self.recorder = None
        self.audio = None
        self._pending_follow_up_request = False
        self._tts_speaking = False
        self._echo_retry_count = 0

    def _close_audio_resources(self):
        with self._audio_lock:
            if self.recorder and hasattr(self.recorder, "stop"):
                try:
                    self.recorder.stop()
                except Exception:
                    pass
            if self._model_prime_thread and self._model_prime_thread.is_alive():
                self._model_prime_thread = None
            if self.recorder and hasattr(self.recorder, "delete"):
                try:
                    self.recorder.delete()
                except Exception:
                    pass
            if self.audio and hasattr(self.audio, "terminate"):
                try:
                    self.audio.terminate()
                except Exception:
                    pass

    def _on_wake(self, topic, data):
        unwrap_event_payload(data)
        self._request_recording("wake_word")

    def _on_listen_for_reply(self, topic, data):
        unwrap_event_payload(data)
        setattr(self.bus, "_voice_followup_active", True)
        if self._tts_speaking:
            self._pending_follow_up_request = True
            return
        self._request_recording("follow_up")

    def _on_tts_state(self, topic, data):
        payload = unwrap_event_payload(data)
        state = str((payload or {}).get("state", "")).strip().lower()
        text = str((payload or {}).get("text", "")).strip()
        if state == "speaking":
            self._tts_speaking = True
            if text:
                self._last_tts_text = text
            return
        if state in {"idle", "stopped", "error"}:
            self._tts_speaking = False
            self._last_tts_finished_at = time.time()
            if text:
                self._last_tts_text = text
            if self._pending_follow_up_request:
                self._pending_follow_up_request = False
                self._request_recording("follow_up")

    def _request_recording(self, source):
        if not self._running:
            return
        if self.audio is None and self.recorder is None:
            if not is_interactive_input():
                logger.debug("STT fallback skipped because stdin is not interactive")
                return
            logger.info("STT: type a reply")
            try:
                text = sys.stdin.readline().strip()
                if text:
                    transcript_payload = {"text": text, "confidence": 1.0, "source": "keyboard"}
                    self.bus.publish("pet/input/speech", transcript_payload)
                    self.bus.publish("pet/voice/transcript", transcript_payload)
            except Exception as e:
                logger.error(f"Error in keyboard fallback: {e}")
            return

        with self._request_lock:
            if self._request_pending:
                return
            if self._voice_state in {
                self.STATE_LISTENING,
                self.STATE_FOLLOW_UP_LISTENING,
                self.STATE_PROCESSING,
                self.STATE_RESPONDING,
            }:
                logger.debug("STT: ignoring %s request while state=%s", source, self._voice_state)
                return
            if not self.record_queue.empty():
                return
            self._request_pending = True
            try:
                self.record_queue.put_nowait(("record", source))
            except queue.Full:
                self._request_pending = False

    def _set_voice_state(self, state, source="", detail=""):
        state = str(state or self.STATE_IDLE).strip().upper() or self.STATE_IDLE
        with self._state_lock:
            if self._voice_state == state:
                return
            self._voice_state = state
        self.bus.publish(
            "pet/voice/state",
            {
                "state": state,
                "source": source,
                "detail": detail,
                "timestamp": time.time(),
            },
        )

    def _publish_state(self, state, detail=""):
        state = str(state or self.STATE_IDLE).strip().upper() or self.STATE_IDLE
        with self._state_lock:
            self._voice_state = state
        self.bus.publish(
            "pet/voice/state",
            {
                "state": state,
                "source": getattr(self, "_current_source", ""),
                "detail": detail,
                "timestamp": time.time(),
            },
        )

    def _prime_model_async(self):
        if self.model is not None:
            return
        if self._model_prime_thread is not None and self._model_prime_thread.is_alive():
            return

        def _load():
            try:
                self._ensure_model_loaded()
            except Exception as exc:
                logger.info("STT: model preload deferred (%s)", exc)

        self._model_prime_thread = threading.Thread(target=_load, daemon=True)
        self._model_prime_thread.start()

    def _ensure_model_loaded(self):
        if self.model is not None:
            return
        try:
            logger.info("STT: loading Whisper model '%s'", self.model_name)
            self.model = WhisperModel(self.model_name, device="cpu", compute_type="int8")
            logger.info("STT: model ready")
        except Exception as exc:
            self.model = None
            raise RuntimeError(
                f"Whisper model failed to load or download: {exc}. Check your internet connection and model path."
            ) from exc

    def _record_and_transcribe(self):
        source = getattr(self, "_current_source", "wake_word")
        listen_state = self.STATE_FOLLOW_UP_LISTENING if source == "follow_up" else self.STATE_LISTENING
        self._set_voice_state(listen_state, source=source)
        wake = getattr(self.bus, "_wake", None)
        if wake is not None and hasattr(wake, "pause_detection"):
            wake.pause_detection()
        try:
            capture = self._capture_speech_frames(source)
            if capture is None or not capture.frames:
                logger.debug("STT: no speech detected")
                return

            self._set_voice_state(self.STATE_PROCESSING, source=source)
            text = self._transcribe_frames(capture)
            if text:
                if self._is_probable_tts_echo(text):
                    logger.info("STT: dropped probable self-echo transcript")
                    if source == "follow_up":
                        self._schedule_follow_up_retry()
                    return
                with self._echo_retry_lock:
                    self._echo_retry_count = 0
                logger.info(f"STT: '{text}'")
                transcript_payload = {"text": text, "confidence": 1.0, "source": "microphone"}
                self.bus.publish("pet/input/speech", transcript_payload)
                self.bus.publish("pet/voice/transcript", transcript_payload)
            else:
                logger.debug("STT: transcript was empty")
        except Exception as e:
            logger.error(f"Transcription error: {e}")
        finally:
            setattr(self.bus, "_voice_followup_active", False)
            if wake is not None and hasattr(wake, "resume_detection"):
                wake.resume_detection()
            self._set_voice_state(self.STATE_IDLE, source=source)

    def _capture_speech_frames(self, source):
        wake = getattr(self.bus, "_wake", None)
        shared_recorder = getattr(wake, "recorder", None) if wake is not None else None
        shared_stream = getattr(wake, "audio_stream", None) if wake is not None else None
        self._using_shared_audio = False
        if shared_recorder is not None or shared_stream is not None:
            self._using_shared_audio = True
            if not self._acquire_mic_lock():
                return None
            if shared_recorder is not None:
                try:
                    frame_length = max(256, int(getattr(shared_recorder, "frame_length", self.frame_length)))
                    return self._capture_from_reader(
                        source=source,
                        reader=lambda: self._read_shared_recorder(shared_recorder),
                        frame_size=frame_length,
                        backend="shared_recorder",
                    )
                finally:
                    self._release_mic_lock()
            if shared_stream is not None:
                try:
                    chunk = max(256, int(self.frame_length))
                    return self._capture_from_reader(
                        source=source,
                        reader=lambda: self._read_shared_stream(shared_stream, chunk),
                        frame_size=chunk,
                        backend="shared_stream",
                    )
                finally:
                    self._release_mic_lock()
        if self.recorder is None and self.audio is None:
            return None
        if self.recorder is not None:
            if not self._start_recording():
                logger.debug("STT: recording skipped while shutting down")
                return None
            try:
                frame_length = max(256, int(getattr(self.recorder, "frame_length", self.frame_length)))
                return self._capture_from_reader(
                    source=source,
                    reader=self._read_recorder,
                    frame_size=frame_length,
                    backend="recorder",
                )
            finally:
                self._stop_recording()

        chunk = 1024
        format = pyaudio.paInt16
        stream = self._open_stream(format, 1, self.sample_rate, chunk)
        if stream is None:
            logger.debug("STT: stream open skipped while shutting down")
            return None
        try:
            return self._capture_from_reader(
                source=source,
                reader=lambda: self._read_stream(stream, chunk),
                frame_size=chunk,
                backend="stream",
            )
        finally:
            self._close_stream(stream)

    def _capture_from_reader(self, source, reader, frame_size, backend):
        frame_size = max(1, int(frame_size))
        silence_frames_required = max(
            1,
            int(round(self.silence_threshold_seconds * self.sample_rate / frame_size)),
        )
        max_wait_frames = max(
            1,
            int(round(self.conversation_window_seconds * self.sample_rate / frame_size)),
        )
        min_speech_frames = max(
            1,
            int(round(self.min_speech_duration_seconds * self.sample_rate / frame_size)),
        )
        pre_roll_frames = max(1, int(round(self.pre_speech_seconds * self.sample_rate / frame_size)))
        baseline_frames = max(6, int(round(0.5 * self.sample_rate / frame_size)))
        pre_roll = deque(maxlen=pre_roll_frames)
        baseline = deque(maxlen=baseline_frames)
        frames = []
        speech_started = False
        speech_start_count = 0
        silence_count = 0
        speech_frames = 0
        waited_frames = 0
        deadline = time.monotonic() + self._max_capture_seconds

        while self._running and time.monotonic() < deadline:
            if not speech_started and waited_frames >= max_wait_frames:
                return None
            try:
                frame = reader()
            except Exception as exc:
                logger.debug("STT: audio read failed during %s capture: %s", source, exc)
                return None
            if frame is None:
                continue

            energy = self._frame_energy(frame)
            threshold = self._dynamic_energy_threshold(baseline)

            if not speech_started:
                waited_frames += 1
                baseline.append(energy)
                pre_roll.append(frame)
                if self._is_speech_frame(energy, threshold):
                    speech_start_count += 1
                else:
                    speech_start_count = 0
                if speech_start_count >= self.speech_start_frames:
                    speech_started = True
                    frames.extend(list(pre_roll))
                    speech_frames = 0
                    silence_count = 0
                continue

            frames.append(frame)
            speech_frames += 1
            if self._is_speech_frame(energy, threshold):
                silence_count = 0
            else:
                silence_count += 1
                if silence_count >= silence_frames_required:
                    if speech_frames >= min_speech_frames:
                        break
                    return None

        if not speech_started or speech_frames < min_speech_frames:
            return None
        return _CaptureResult(frames=frames, backend=backend)

    def _dynamic_energy_threshold(self, baseline):
        if baseline:
            ambient = float(np.median(np.asarray(list(baseline), dtype=np.float32)))
        else:
            ambient = self._min_energy_threshold
        return max(self._min_energy_threshold, ambient * self._noise_multiplier)

    @staticmethod
    def _is_speech_frame(energy, threshold):
        return float(energy) >= float(threshold)

    @staticmethod
    def _frame_energy(frame):
        if isinstance(frame, bytes):
            samples = np.frombuffer(frame, dtype=np.int16)
        else:
            samples = np.asarray(frame, dtype=np.int16)
        if samples.size == 0:
            return 0.0
        return float(np.sqrt(np.mean(np.square(samples.astype(np.float32)))))

    def _transcribe_frames(self, capture):
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
            path = handle.name
        try:
            pcm = self._build_pcm_capture(capture)
            with wave.open(path, "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setframerate(self.sample_rate)
                wav_file.setsampwidth(2)
                wav_file.writeframes(pcm.tobytes())
            return self.transcribe(path)
        finally:
            try:
                if os.path.exists(path):
                    os.unlink(path)
            except Exception:
                pass

    def _build_pcm_capture(self, capture):
        if "recorder" in str(capture.backend):
            samples = np.asarray(capture.frames, dtype=np.int16).reshape(-1)
        else:
            raw = b"".join(capture.frames)
            samples = np.frombuffer(raw, dtype=np.int16)
        return self._preprocess_pcm(samples)

    def _preprocess_pcm(self, samples):
        if samples.size == 0:
            return samples.astype(np.int16)
        pcm = samples.astype(np.float32)
        pcm -= float(np.mean(pcm))
        if self.stt_apply_gain:
            rms = float(np.sqrt(np.mean(np.square(pcm))))
            if rms > 1.0:
                gain = min(self.stt_max_gain, self.stt_target_rms / rms)
                pcm *= gain
        return np.clip(pcm, -32768.0, 32767.0).astype(np.int16)

    @profile
    def transcribe(self, audio_path):
        self._ensure_model_loaded()
        base_kwargs = {
            "beam_size": self.stt_beam_size,
            "condition_on_previous_text": False,
            "vad_filter": True,
            "word_timestamps": False,
            "temperature": 0.0,
            "compression_ratio_threshold": 2.4,
            "log_prob_threshold": -1.0,
            "no_speech_threshold": self.stt_no_speech_threshold,
        }
        if self.stt_best_of > 1 and self._supports_transcribe_kwarg("best_of"):
            base_kwargs["best_of"] = self.stt_best_of
        if self.stt_language != "auto":
            base_kwargs["language"] = self.stt_language
        initial_prompt = str(self.voice_config.get("stt_initial_prompt", "")).strip()
        if initial_prompt:
            base_kwargs["initial_prompt"] = initial_prompt

        segments, _ = self.model.transcribe(audio_path, **base_kwargs)
        segment_list = list(segments)
        result = " ".join([s.text for s in segment_list]).strip()

        if self.stt_retry_on_low_confidence and self._should_retry_decode(segment_list, result):
            retry_kwargs = dict(base_kwargs)
            retry_kwargs["beam_size"] = max(base_kwargs["beam_size"], self.stt_retry_beam_size)
            retry_segments, _ = self.model.transcribe(audio_path, **retry_kwargs)
            retry_list = list(retry_segments)
            retry_result = " ".join([s.text for s in retry_list]).strip()
            if retry_result and self._score_transcript(retry_result) >= self._score_transcript(result):
                result = retry_result

        if not self._is_transcript_plausible(result):
            logger.debug("STT: filtered implausible transcript for language=%s", self.stt_language)
            return ""
        return result

    def _supports_transcribe_kwarg(self, name):
        if name in self._supports_kwarg_cache:
            return self._supports_kwarg_cache[name]
        supported = False
        try:
            params = signature(self.model.transcribe).parameters
            supported = name in params or any(p.kind.name == "VAR_KEYWORD" for p in params.values())
        except Exception:
            supported = False
        self._supports_kwarg_cache[name] = supported
        return supported

    def _should_retry_decode(self, segment_list, text):
        if not text:
            return True
        if self.stt_language == "en" and self.stt_reject_non_latin_when_english and not self._is_transcript_plausible(text):
            return True
        if not segment_list:
            return True
        avg_logprobs = [
            float(getattr(segment, "avg_logprob", 0.0))
            for segment in segment_list
            if getattr(segment, "avg_logprob", None) is not None
        ]
        if avg_logprobs and (sum(avg_logprobs) / max(1, len(avg_logprobs))) < self.stt_low_confidence_logprob:
            return True
        return False

    def _is_transcript_plausible(self, text):
        cleaned = str(text or "").strip()
        if not cleaned:
            return False
        if self.stt_language != "en" or not self.stt_reject_non_latin_when_english:
            return True
        letters = [c for c in cleaned if c.isalpha()]
        if not letters:
            return True
        latin_letters = [c for c in letters if "a" <= c.lower() <= "z"]
        latin_ratio = len(latin_letters) / max(1, len(letters))
        return latin_ratio >= self.stt_min_latin_ratio

    @staticmethod
    def _score_transcript(text):
        candidate = str(text or "").strip()
        if not candidate:
            return 0.0
        letters = sum(1 for char in candidate if char.isalpha())
        spaces = candidate.count(" ")
        return float(letters + spaces * 0.15)

    def _run(self):
        while self._running:
            try:
                action, source = self.record_queue.get(timeout=0.1)
            except queue.Empty:
                continue

            with self._request_lock:
                self._request_pending = False

            if action == "record":
                try:
                    self._current_source = source
                    self._record_and_transcribe()
                except Exception as e:
                    if not self._running:
                        break
                    logger.error(f"STT worker error: {e}")
                    if "Failed to read from device" in str(e):
                        logger.warning("STT: disabling mic input after device read failure")
                        self._close_audio_resources()
                        self._running = False
                        break

    def _start_recording(self):
        if not self._acquire_mic_lock():
            return False
        if self._using_shared_audio:
            return True
        with self._audio_lock:
            if self.recorder is not None and hasattr(self.recorder, "start"):
                self.recorder.start()
        return True

    def _stop_recording(self):
        if self._using_shared_audio:
            self._release_mic_lock()
            return
        with self._audio_lock:
            if self.recorder is not None and hasattr(self.recorder, "stop"):
                try:
                    self.recorder.stop()
                except Exception:
                    pass
        self._release_mic_lock()

    def _read_recorder(self):
        with self._audio_lock:
            return self.recorder.read()

    def _read_shared_recorder(self, recorder):
        with self._audio_lock:
            return recorder.read()

    def _read_shared_stream(self, stream, chunk):
        with self._audio_lock:
            return stream.read(chunk, exception_on_overflow=False)

    def _acquire_mic_lock(self):
        if self._mic_lock is None:
            return True
        deadline = time.time() + self._mic_lock_timeout
        while self._running:
            try:
                if self._mic_lock.acquire(timeout=0.1):
                    return True
            except Exception:
                break
            if time.time() >= deadline:
                break
        return False

    def _release_mic_lock(self):
        if self._mic_lock is None:
            return
        try:
            self._mic_lock.release()
        except Exception:
            pass

    def _open_stream(self, format, channels, rate, chunk):
        if not self._acquire_mic_lock():
            return None
        with self._audio_lock:
            try:
                return self.audio.open(
                    format=format,
                    channels=channels,
                    rate=rate,
                    input=True,
                    frames_per_buffer=chunk,
                )
            except Exception:
                self._release_mic_lock()
                raise

    def _read_stream(self, stream, chunk):
        return stream.read(chunk, exception_on_overflow=False)

    def _close_stream(self, stream):
        with self._audio_lock:
            try:
                stream.stop_stream()
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass
        if self._mic_lock is not None:
            self._release_mic_lock()

    def _schedule_follow_up_retry(self):
        with self._echo_retry_lock:
            if self._echo_retry_count >= self.echo_retry_limit:
                logger.debug("STT: follow-up echo retry limit reached")
                self._echo_retry_count = 0
                return
            self._echo_retry_count += 1
            thread = self._follow_up_retry_thread
            if thread is not None and thread.is_alive():
                return

        wait_for_guard = max(0.0, self.echo_guard_seconds - (time.time() - self._last_tts_finished_at))
        delay = wait_for_guard + self.echo_retry_backoff_seconds

        def _retry():
            if delay > 0:
                time.sleep(delay)
            if not self._running:
                return
            setattr(self.bus, "_voice_followup_active", True)
            self.bus.publish(
                "pet/voice/listen_for_reply",
                {
                    "source": "stt_echo_guard",
                    "timestamp": time.time(),
                },
            )

        self._follow_up_retry_thread = threading.Thread(target=_retry, daemon=True)
        self._follow_up_retry_thread.start()

    def _is_probable_tts_echo(self, transcript_text):
        now = time.time()
        if self._tts_speaking:
            return True
        if not self._last_tts_text:
            return False
        if now - self._last_tts_finished_at > self.echo_guard_seconds:
            return False
        heard = self._normalize_for_similarity(transcript_text)
        spoken = self._normalize_for_similarity(self._last_tts_text)
        if len(heard) < self.echo_min_chars or len(spoken) < self.echo_min_chars:
            return False
        if heard in spoken or spoken in heard:
            return True
        similarity = SequenceMatcher(a=heard, b=spoken).ratio()
        return similarity >= self.echo_similarity_threshold

    @staticmethod
    def _normalize_for_similarity(text):
        cleaned = re.sub(r"[^a-z0-9\s]+", " ", str(text or "").strip().lower())
        return " ".join(cleaned.split())
