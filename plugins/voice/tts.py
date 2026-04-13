import logging
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time

from core.platform_utils import resolve_executable
from core.utils import unwrap_event_payload

logger = logging.getLogger(__name__)

# Piper is a command-line tool; the runtime check happens against the executable
# path so we can support platform-specific installs and custom paths.
PIPER_AVAILABLE = True

try:
    import pyttsx3

    PYTTSX3_AVAILABLE = True
except ImportError:
    pyttsx3 = None
    PYTTSX3_AVAILABLE = False


class TextToSpeech:
    def __init__(self, bus, hal, memory, config):
        self.bus = bus
        self.hal = hal
        self.memory = memory
        self.config = config
        self._running = True
        self._queue = queue.Queue(maxsize=8)
        self._thread = None
        self._current_process = None
        self._play_lock = threading.Lock()
        self._interrupt_event = threading.Event()
        self._interrupt_on_new_speech = config.get("voice", {}).get("interrupt_on_new_speech", True)
        self.voice_model = config.get("voice", {}).get("tts_model", "en_US-lessac-medium")
        # Piper path (assume installed)
        self.piper_path = config.get("voice", {}).get("piper_path", "piper")
        self.speed_multiplier = 1.0  # adjust based on emotion
        self._system_tts = None
        if PYTTSX3_AVAILABLE:
            try:
                self._system_tts = pyttsx3.init()
            except Exception as e:
                logger.warning(f"pyttsx3 initialisation failed: {e}")
                self._system_tts = None

    def start(self):
        self._thread = threading.Thread(target=self._run)
        self._thread.daemon = True
        self._thread.start()
        self.bus.subscribe("pet/speak/say", self._on_say)
        self.bus.subscribe("pet/speak/stop", self._on_stop)
        logger.info("TTS started")

    def stop(self):
        self._running = False
        self._stop_playback()
        setattr(self.bus, "_voice_followup_active", False)
        if self._thread:
            self._thread.join(timeout=1)

    def _on_say(self, topic, data):
        data = unwrap_event_payload(data)
        text = data.get("text", "")
        if not text:
            return
        if self._interrupt_on_new_speech:
            self._interrupt_event.set()
            self._clear_queue()
            self._stop_playback()
        emotion = data.get("emotion", "neutral")
        listen_after = bool(data.get("listen_after", False))
        # Adjust speed based on emotion (example)
        if emotion in ["excited", "happy"]:
            speed = 1.2
        elif emotion in ["sad", "sleepy"]:
            speed = 0.8
        else:
            speed = 1.0
        try:
            self._queue.put_nowait((text, speed, listen_after))
        except queue.Full:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            self._queue.put_nowait((text, speed, listen_after))
        finally:
            self._interrupt_event.clear()

    def _on_stop(self, topic, data):
        data = unwrap_event_payload(data)
        self._interrupt_event.set()
        self._clear_queue()
        self._stop_playback()
        setattr(self.bus, "_voice_followup_active", False)
        self._publish_state("stopped", "")

    def _run(self):
        while self._running:
            try:
                text, speed, listen_after = self._queue.get(timeout=0.1)
                if self._interrupt_event.is_set():
                    self._interrupt_event.clear()
                    continue
                self._publish_state("speaking", text)
                if self._piper_is_available():
                    self._speak_piper(text, speed)
                elif self._system_tts is not None:
                    self._speak_system_tts(text, speed)
                else:
                    self._print_text(text)
                if listen_after:
                    setattr(self.bus, "_voice_followup_active", True)
                    self._publish_listen_for_reply(text)
                else:
                    setattr(self.bus, "_voice_followup_active", False)
                self._publish_state("idle", text)
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"TTS error: {e}")
                self._publish_state("error", str(e))

    def _speak_piper(self, text, speed):
        # Piper expects text on stdin, outputs audio to stdout or file.
        # We'll use a temporary file for audio and play with aplay/afplay.
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            audio_file = f.name
        piper_executable = resolve_executable(self.piper_path)
        if not piper_executable:
            raise FileNotFoundError(f"Piper executable not found: {self.piper_path}")

        cmd = [piper_executable, "--model", self.voice_model, "--output_file", audio_file]
        # Speed is not directly supported by piper; we could use sox or adjust playback.
        # For simplicity, we'll just pass text.
        try:
            subprocess.run(cmd, input=text.encode("utf-8"), capture_output=True, check=True)
            # Play audio
            if os.name == "nt":
                import winsound

                winsound.PlaySound(audio_file, winsound.SND_FILENAME)
            else:
                self._play_audio_file(audio_file)
        except Exception as e:
            logger.warning(f"Piper execution error, falling back to terminal output: {e}")
            if self._system_tts is not None:
                self._speak_system_tts(text, speed)
            else:
                self._print_text(text)
        finally:
            try:
                os.unlink(audio_file)
            except OSError:
                pass

    def _piper_is_available(self):
        if not PIPER_AVAILABLE:
            return False
        return resolve_executable(self.piper_path) is not None

    def _print_text(self, text):
        print(f"\n[E-Pet says]: {text}")

    def _speak_system_tts(self, text, speed):
        try:
            self._system_tts.setProperty("rate", int(170 * speed))
            self._system_tts.say(text)
            self._system_tts.runAndWait()
        except Exception as e:
            logger.warning(f"System TTS failed, falling back to terminal output: {e}")
            self._print_text(text)

    def _play_audio_process(self, cmd):
        player = resolve_executable(cmd[0])
        if not player:
            raise FileNotFoundError(f"Audio player not found: {cmd[0]}")
        cmd = [player, *cmd[1:]]
        with self._play_lock:
            self._current_process = subprocess.Popen(cmd)
        try:
            while self._current_process.poll() is None:
                if self._interrupt_event.is_set():
                    self._current_process.terminate()
                    break
                time.sleep(0.1)
        finally:
            with self._play_lock:
                self._current_process = None

    def _stop_playback(self):
        with self._play_lock:
            process = self._current_process
            self._current_process = None
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1)
            except Exception:
                process.kill()

    def _play_audio_file(self, audio_file):
        tried = []
        for candidate in self._audio_player_candidates():
            player = resolve_executable(candidate[0])
            if not player:
                tried.append(candidate[0])
                continue
            try:
                self._play_audio_process([player, *candidate[1:], audio_file])
                return
            except Exception as e:
                logger.debug(f"Audio player {candidate[0]} failed: {e}")
            tried.append(candidate[0])
        raise FileNotFoundError(f"No supported audio playback command was found. Tried: {', '.join(tried) or 'none'}")

    @staticmethod
    def _audio_player_candidates():
        if sys.platform == "darwin":
            return [["afplay"]]
        return [
            ["aplay"],
            ["paplay"],
            ["ffplay", "-autoexit", "-nodisp", "-loglevel", "quiet"],
        ]

    def _clear_queue(self):
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break

    def _publish_state(self, state, text):
        self.bus.publish(
            "pet/voice/tts_state",
            {
                "state": state,
                "text": text,
                "timestamp": time.time(),
            },
        )

    def _publish_listen_for_reply(self, text):
        self.bus.publish(
            "pet/voice/listen_for_reply",
            {
                "source": "tts",
                "text": text,
                "timestamp": time.time(),
            },
        )
