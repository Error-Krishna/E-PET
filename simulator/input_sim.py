import logging
import threading
import sys
import time

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
        # Use raw terminal input (Unix) or msvcrt (Windows)
        if sys.platform == 'win32':
            import msvcrt
            while self._running:
                if msvcrt.kbhit():
                    ch = msvcrt.getch().decode('ascii', errors='ignore').lower()
                    self._handle_key(ch)
                time.sleep(0.01)
        else:
            import tty, termios
            fd = sys.stdin.fileno()
            old_settings = termios.tcgetattr(fd)
            try:
                tty.setraw(fd)
                while self._running:
                    ch = sys.stdin.read(1).lower()
                    self._handle_key(ch)
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

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