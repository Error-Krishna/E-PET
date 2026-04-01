import logging
import threading
import time
from typing import Dict, Callable

logger = logging.getLogger(__name__)

# Try to import numpy and pygame; if missing, disable sound
try:
    import numpy as np
    import pygame
    SOUND_AVAILABLE = True
except ImportError as e:
    logger.warning(f"Sound engine disabled: {e}")
    SOUND_AVAILABLE = False

class SoundEngine:
    """Synthesizes sounds using numpy and pygame."""
    def __init__(self, bus, hal, memory, config):
        self.bus = bus
        self.hal = hal
        self.memory = memory
        self.config = config
        self._running = True
        self.audio_enabled = SOUND_AVAILABLE

        # Sound generators: mapping name -> function that returns a numpy array of samples
        self.sounds: Dict[str, Callable[[], np.ndarray]] = {
            "purr": self._purr,
            "chirp": self._chirp,
            "squeak": self._squeak,
            "boop": self._boop,
            "chime": self._chime,
            "alert": self._alert,
            "sad_whimper": self._sad_whimper,
            "excited_trill": self._excited_trill,
            "thinking_hum": self._thinking_hum,
            "notification": self._notification,
            "startup": self._startup,
            "shutdown": self._shutdown,
        }

        if self.audio_enabled:
            try:
                pygame.mixer.init(frequency=22050, size=-16, channels=1)
                logger.info("Pygame mixer initialised")
            except pygame.error as e:
                self.audio_enabled = False
                logger.warning(f"Sound engine running in dummy mode: {e}")
        else:
            logger.warning("Sound engine running in dummy mode (no audio)")

    def start(self):
        self.bus.subscribe("pet/sound/play", self._on_play_sound)
        self.bus.subscribe("pet/emotion/changed", self._on_emotion_changed)
        logger.info("Sound engine started")

    def stop(self):
        self._running = False
        if self.audio_enabled:
            pygame.mixer.quit()

    def _on_play_sound(self, topic, data):
        name = data.get("name")
        if name in self.sounds:
            self._play_sound(name)
        else:
            logger.warning(f"Unknown sound: {name}")

    def _on_emotion_changed(self, topic, data):
        sound = data.get("sound")
        if sound and sound in self.sounds:
            self._play_sound(sound)
        # Also log to HAL for tracking
        self.hal.play_sound(sound)

    def _play_sound(self, name):
        """Generate and play sound in a separate thread to avoid blocking."""
        if not self.audio_enabled:
            logger.debug(f"Sound would play: {name}")
            return
        def play():
            try:
                samples = self.sounds[name]()
                # Convert to 16-bit ints
                samples = (samples * 32767).astype(np.int16)
                # Create pygame Sound object
                sound = pygame.sndarray.make_sound(samples)
                sound.play()
            except Exception as e:
                logger.error(f"Error playing sound {name}: {e}")
        thread = threading.Thread(target=play)
        thread.daemon = True
        thread.start()

    # ----- Sound synthesis functions -----
    @staticmethod
    def _generate_tone(freq, duration, sample_rate=22050, wave="sine", envelope="attack_decay"):
        t = np.linspace(0, duration, int(sample_rate * duration))
        if wave == "sine":
            wave = np.sin(2 * np.pi * freq * t)
        elif wave == "square":
            wave = np.sign(np.sin(2 * np.pi * freq * t))
        elif wave == "saw":
            wave = 2 * (freq * t - np.floor(0.5 + freq * t))
        elif wave == "triangle":
            wave = 2 * np.abs(2 * (freq * t - np.floor(0.5 + freq * t))) - 1
        else:
            wave = np.sin(2 * np.pi * freq * t)

        # Apply envelope
        if envelope == "attack_decay":
            attack = 0.02
            decay = 0.1
            env = np.minimum(1.0, t / attack) * np.exp(-t / decay)
        elif envelope == "attack_release":
            attack = 0.02
            release = 0.2
            env = np.minimum(1.0, t / attack) * (1 - np.exp(-t / release))
        else:
            env = 1.0
        return wave * env

    @staticmethod
    def _time_axis(duration, sample_rate=22050):
        return np.linspace(0, duration, int(sample_rate * duration))

    def _purr(self):
        # Low-frequency rumble with harmonics
        t = self._time_axis(1.5)
        freq = 30
        sig = np.sin(2 * np.pi * freq * t)
        for h in [2, 3, 4]:
            sig += 0.3 * np.sin(2 * np.pi * freq * h * t)
        env = np.exp(-t / 0.5)  # quick decay then sustain? Actually purr is sustained; we'll loop
        # Loop for continuous purr? We'll just do 1.5s
        sig = sig * env
        return sig / np.max(np.abs(sig))

    def _chirp(self):
        # Short high-pitched chirp
        freq = 800
        duration = 0.2
        sig = self._generate_tone(freq, duration, wave="sine", envelope="attack_release")
        return sig

    def _squeak(self):
        # Short, high frequency, fast decay
        freq = 1500
        duration = 0.15
        sig = self._generate_tone(freq, duration, wave="sine", envelope="attack_decay")
        return sig

    def _boop(self):
        # Medium beep
        freq = 500
        duration = 0.2
        sig = self._generate_tone(freq, duration, wave="sine", envelope="attack_decay")
        return sig

    def _chime(self):
        # Clear, bell-like
        t = self._time_axis(0.8)
        freq = 880
        sig = np.sin(2 * np.pi * freq * t) * np.exp(-t / 0.2)
        # Add overtone
        sig += 0.3 * np.sin(2 * np.pi * freq * 2 * t) * np.exp(-t / 0.15)
        return sig / np.max(np.abs(sig))

    def _alert(self):
        # Siren-like, sweeping
        t = self._time_axis(1.0)
        freq = 800 + 200 * np.sin(2 * np.pi * 4 * t)
        sig = np.sin(2 * np.pi * freq * t) * np.exp(-t / 0.5)
        return sig / np.max(np.abs(sig))

    def _sad_whimper(self):
        # Descending, mournful
        t = self._time_axis(0.8)
        freq = 400 - 200 * t
        sig = np.sin(2 * np.pi * freq * t) * np.exp(-t / 0.3)
        return sig / np.max(np.abs(sig))

    def _excited_trill(self):
        # Rapid alternating high/low
        t = self._time_axis(0.5)
        freq = 1000 + 200 * np.sin(2 * np.pi * 20 * t)
        sig = np.sin(2 * np.pi * freq * t) * np.exp(-t / 0.1)
        return sig / np.max(np.abs(sig))

    def _thinking_hum(self):
        # Low, steady hum
        t = self._time_axis(1.0)
        freq = 100
        sig = np.sin(2 * np.pi * freq * t) * (0.5 + 0.5 * np.sin(2 * np.pi * 2 * t))
        env = 1 - np.exp(-t / 0.1)
        sig = sig * env
        return sig / np.max(np.abs(sig))

    def _notification(self):
        # Short double beep
        beep = self._boop()
        # Pad with silence
        silence = np.zeros(int(22050 * 0.05))
        combined = np.concatenate([beep, silence, beep])
        return combined

    def _startup(self):
        # Ascending scale
        t = self._time_axis(0.8)
        freqs = [440, 554, 659, 880]
        sig = np.zeros_like(t)
        duration_per = 0.2
        for i, f in enumerate(freqs):
            start = i * duration_per
            end = start + duration_per
            mask = (t >= start) & (t < end)
            sig[mask] = np.sin(2 * np.pi * f * (t[mask] - start)) * np.exp(-(t[mask] - start) / 0.05)
        return sig / np.max(np.abs(sig))

    def _shutdown(self):
        # Descending scale
        t = self._time_axis(0.8)
        freqs = [880, 659, 554, 440]
        sig = np.zeros_like(t)
        duration_per = 0.2
        for i, f in enumerate(freqs):
            start = i * duration_per
            end = start + duration_per
            mask = (t >= start) & (t < end)
            sig[mask] = np.sin(2 * np.pi * f * (t[mask] - start)) * np.exp(-(t[mask] - start) / 0.05)
        return sig / np.max(np.abs(sig))

def start(bus, hal, memory, config):
    engine = SoundEngine(bus, hal, memory, config)
    engine.start()
    # Keep reference
    bus._sound_engine = engine
    return engine
