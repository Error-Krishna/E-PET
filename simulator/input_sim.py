import logging
import threading
import sys
import time
import os

logger = logging.getLogger(__name__)

# Key mappings (keep as before)
KEY_MAPPINGS = {
    'h': ('pet/input/touch', {'zone': 'head'}),
    'c': ('pet/input/touch', {'zone': 'chin'}),
    'b': ('pet/input/touch', {'zone': 'back'}),
    'e': ('pet/input/touch', {'zone': 'belly'}),
    'p': ('pet/input/touch', {'zone': 'poke'}),
    's': ('pet/input/touch', {'zone': 'shake'}),
    'l': ('pet/input/touch', {'zone': 'hold'}),
    'd': ('pet/input/touch', {'zone': 'double_tap'}),
    'm': ('pet/input/keyboard', {'action': 'cycle_mood'}),
    't': ('pet/input/keyboard', {'action': 'test_sound'}),
    ' ': ('pet/input/wake_word', {'source': 'keyboard'}),
    'q': ('pet/system/quit', {}),
}

class InputSimulator:
    def __init__(self, bus):
        self.bus = bus
        self._running = True
        self._thread = None
        self._quit_sent = False

    def start(self):
        self._thread = threading.Thread(target=self._run)
        self._thread.daemon = True
        self._thread.start()
        logger.info("Input simulator started")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=1)

    def _run(self):
        # Use raw terminal input (Unix) or msvcrt (Windows) when stdin is interactive.
        if not sys.stdin.isatty():
            logger.warning("Input simulator disabled because stdin is not interactive")
            while self._running:
                time.sleep(0.2)
            return

        if os.name == "nt":
            self._run_windows()
        else:
            self._run_posix()

    def _run_windows(self):
        try:
            import msvcrt
        except Exception as e:
            logger.warning(f"Windows keyboard input unavailable: {e}")
            return self._idle_loop()

        while self._running:
            if msvcrt.kbhit():
                ch = msvcrt.getwch().lower()
                self._handle_key(ch)
            time.sleep(0.01)

    def _run_posix(self):
        try:
            import tty
            import termios
        except Exception as e:
            logger.warning(f"POSIX keyboard input unavailable: {e}")
            return self._idle_loop()

        try:
            fd = sys.stdin.fileno()
            old_settings = termios.tcgetattr(fd)
        except Exception as e:
            logger.warning(f"Terminal raw mode unavailable: {e}")
            return self._idle_loop()

        try:
            tty.setraw(fd)
            while self._running:
                ch = sys.stdin.read(1)
                if not ch:
                    continue
                self._handle_key(ch.lower())
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

    def _idle_loop(self):
        while self._running:
            time.sleep(0.2)

    def _handle_key(self, ch):
        if ch in KEY_MAPPINGS:
            topic, data = KEY_MAPPINGS[ch]
            # Avoid repeated quit events
            if topic == "pet/system/quit":
                if self._quit_sent:
                    return
                self._quit_sent = True
            # Add timestamp to touch events
            if topic == "pet/input/touch":
                data = dict(data)
                data['timestamp'] = time.time()
            self.bus.publish(topic, data)
