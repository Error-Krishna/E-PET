import logging
import threading
import time
import sys
import queue
import tempfile
import wave
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
        self.recorder = None
        self.record_queue = queue.Queue(maxsize=1)
        self.voice_config = config.get("voice", {})
        self.model_name = self.voice_config.get("whisper_model", "tiny")
        self._mic_lock = getattr(bus, "_mic_lock", None)
        self._mic_lock_timeout = max(0.5, float(self.voice_config.get("mic_lock_timeout", 5.0)))
        if WHISPER_AVAILABLE:
            try:
                self.model = WhisperModel(self.model_name, device="cpu", compute_type="int8")
                self._init_audio_backend()
            except Exception as e:
                logger.info(f"STT: falling back to keyboard input ({e})")
                self.model = None
                self.audio = None
                self.recorder = None
        else:
            logger.info("STT: keyboard fallback")

    def _init_audio_backend(self):
        if PvRecorder is not None:
            try:
                self.recorder = PvRecorder(device_index=-1, frame_length=1024)
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
        logger.info("STT: ready")

    def stop(self):
        self._running = False
        self._close_audio_resources()
        setattr(self.bus, "_voice_followup_active", False)
        if self._thread:
            self._thread.join(timeout=1)
        self._thread = None
        self.recorder = None
        self.audio = None

    def _close_audio_resources(self):
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
        if self.audio and hasattr(self.audio, "terminate"):
            try:
                self.audio.terminate()
            except Exception:
                pass

    def _on_wake(self, topic, data):
        data = unwrap_event_payload(data)
        self._request_recording("wake_word")

    def _on_listen_for_reply(self, topic, data):
        data = unwrap_event_payload(data)
        setattr(self.bus, "_voice_followup_active", True)
        self._request_recording("follow_up")

    def _request_recording(self, source):
        # Wake word or follow-up trigger recording
        if self.model is not None and (self.audio is not None or self.recorder is not None):
            if self.record_queue.empty():
                self.record_queue.put_nowait(("record", source))
        elif WHISPER_AVAILABLE and (self.audio is not None or self.recorder is not None):
            if self.record_queue.empty():
                self.record_queue.put_nowait(("record", source))
        else:
            # Fallback: read from terminal input
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

    def _record_and_transcribe(self):
        # Record audio for a few seconds (adjustable)
        channels = 1
        rate = 16000
        record_seconds = self.voice_config.get("record_seconds", 3)
        frames = []
        logger.debug("Recording started")
        wake = getattr(self.bus, "_wake", None)
        if wake is not None and hasattr(wake, "pause_detection"):
            wake.pause_detection()
        try:
            if self.recorder is None and self.audio is None:
                logger.debug("STT: no audio backend available, skipping record")
                return
            if self.recorder is not None:
                frame_length = getattr(self.recorder, "frame_length", 1024)
                logger.info("STT: recording")
                if not self._start_recording():
                    logger.debug("STT: recording skipped while shutting down")
                    return
                try:
                    for _ in range(0, int(rate / frame_length * record_seconds)):
                        data = self._read_recorder()
                        frames.extend(data)
                finally:
                    self._stop_recording()
                logger.debug("Recording finished")
            else:
                chunk = 1024
                format = pyaudio.paInt16
                logger.info("STT: recording")
                stream = self._open_stream(format, channels, rate, chunk)
                if stream is None:
                    logger.debug("STT: stream open skipped while shutting down")
                    return
                try:
                    for _ in range(0, int(rate / chunk * record_seconds)):
                        data = self._read_stream(stream, chunk)
                        frames.append(data)
                finally:
                    self._close_stream(stream)
                logger.debug("Recording finished")
            # Save to temporary file
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                wf = wave.open(f.name, "wb")
                wf.setnchannels(channels)
                wf.setframerate(rate)
                if self.recorder is not None:
                    wf.setsampwidth(2)
                    wf.writeframes(np.asarray(frames, dtype=np.int16).tobytes())
                else:
                    wf.setsampwidth(self.audio.get_sample_size(format))
                    wf.writeframes(b"".join(frames))
                wf.close()
                # Transcribe
                try:
                    text = self.transcribe(f.name)
                    if text:
                        logger.info(f"STT: '{text}'")
                        transcript_payload = {"text": text, "confidence": 1.0, "source": "microphone"}
                        self.bus.publish(
                            "pet/input/speech",
                            transcript_payload,
                        )
                        self.bus.publish(
                            "pet/voice/transcript",
                            transcript_payload,
                        )
                    else:
                        logger.debug("STT: no speech detected")
                except Exception as e:
                    logger.error(f"Transcription error: {e}")
        finally:
            setattr(self.bus, "_voice_followup_active", False)
            if wake is not None and hasattr(wake, "resume_detection"):
                wake.resume_detection()

    @profile
    def transcribe(self, audio_path):
        if self.model is None:
            raise RuntimeError("STT model is not available")
        segments, _ = self.model.transcribe(audio_path, beam_size=1)
        result = " ".join([s.text for s in segments]).strip()
        return result

    def _run(self):
        while self._running:
            try:
                action, source = self.record_queue.get(timeout=0.1)
            except queue.Empty:
                continue

            if action == "record":
                try:
                    if source == "follow_up":
                        logger.info("STT: listening for reply")
                    self._record_and_transcribe()
                except Exception as e:
                    if not self._running:
                        break
                    logger.error(f"STT worker error: {e}")
                    if "Failed to read from device" in str(e):
                        logger.warning("STT: disabling mic input after device read failure")
                        self._running = False
                        break

    def _start_recording(self):
        if not self._acquire_mic_lock():
            return False
        if self.recorder is not None and hasattr(self.recorder, "start"):
            self.recorder.start()
        return True

    def _stop_recording(self):
        if self.recorder is not None and hasattr(self.recorder, "stop"):
            try:
                self.recorder.stop()
            except Exception:
                pass
        self._release_mic_lock()

    def _read_recorder(self):
        return self.recorder.read()

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
        if self._mic_lock is None:
            return self.audio.open(
                format=format,
                channels=channels,
                rate=rate,
                input=True,
                frames_per_buffer=chunk,
            )
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
        try:
            stream.stop_stream()
        except Exception:
            pass
        try:
            stream.close()
        except Exception:
            pass
        if self._mic_lock is not None and self._mic_lock.locked():
            self._release_mic_lock()
