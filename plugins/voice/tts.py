import logging
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time

logger = logging.getLogger(__name__)

# Try to import piper; fallback to print
try:
    # Piper is command-line tool; we'll use subprocess
    import piper  # noqa: F401

    PIPER_AVAILABLE = True
except ImportError:
    PIPER_AVAILABLE = False
    logger.warning("Piper not installed; TTS will print text to terminal")


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
        self._interrupt_on_new_speech = config.get("voice", {}).get("interrupt_on_new_speech", True)
        self.voice_model = config.get("voice", {}).get("tts_model", "en_US-lessac-medium")
        # Piper path (assume installed)
        self.piper_path = config.get("voice", {}).get("piper_path", "piper")
        self.speed_multiplier = 1.0  # adjust based on emotion

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
        if self._thread:
            self._thread.join(timeout=1)

    def _on_say(self, topic, data):
        text = data.get("text", "")
        if not text:
            return
        if self._interrupt_on_new_speech:
            self._clear_queue()
            self._stop_playback()
        emotion = data.get("emotion", "neutral")
        # Adjust speed based on emotion (example)
        if emotion in ["excited", "happy"]:
            speed = 1.2
        elif emotion in ["sad", "sleepy"]:
            speed = 0.8
        else:
            speed = 1.0
        try:
            self._queue.put_nowait((text, speed))
        except queue.Full:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            self._queue.put_nowait((text, speed))

    def _on_stop(self, topic, data):
        self._clear_queue()
        self._stop_playback()
        self._publish_state("stopped", "")

    def _run(self):
        while self._running:
            try:
                text, speed = self._queue.get(timeout=0.1)
                self._publish_state("speaking", text)
                if PIPER_AVAILABLE:
                    self._speak_piper(text, speed)
                else:
                    self._print_text(text)
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
        cmd = [self.piper_path, "--model", self.voice_model, "--output_file", audio_file]
        # Speed is not directly supported by piper; we could use sox or adjust playback.
        # For simplicity, we'll just pass text.
        try:
            subprocess.run(cmd, input=text.encode(), capture_output=True, check=True)
            # Play audio
            if sys.platform == "darwin":
                self._play_audio_process(["afplay", audio_file])
            elif sys.platform.startswith("linux"):
                self._play_audio_process(["aplay", audio_file])
            elif sys.platform == "win32":
                import winsound

                winsound.PlaySound(audio_file, winsound.SND_FILENAME)
            else:
                logger.warning("Unsupported platform for audio playback")
        except Exception as e:
            logger.warning(f"Piper execution error, falling back to terminal output: {e}")
            self._print_text(text)
        finally:
            if os.path.exists(audio_file):
                os.unlink(audio_file)

    def _print_text(self, text):
        print(f"\n[E-Pet says]: {text}")

    def _play_audio_process(self, cmd):
        with self._play_lock:
            self._current_process = subprocess.Popen(cmd)
        try:
            self._current_process.wait()
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
