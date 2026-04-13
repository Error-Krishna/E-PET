import logging
import queue
import threading
import time
from typing import Dict, Callable

from core.utils import unwrap_event_payload

logger = logging.getLogger(__name__)

# Try to import numpy and pygame; if missing, disable sound
try:
    import numpy as np
    import pygame
    SOUND_AVAILABLE = True
except ImportError as e:
    logger.debug(f"Sound engine disabled: {e}")
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
        self._sound_thread = None
        self._play_queue: queue.Queue[str | None] = queue.Queue(maxsize=32)
        self._mixer_ready = threading.Event()

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

        self._sound_cache = {}
        if self.audio_enabled:
            logger.info("Sound: audio pending initialization")
        else:
            logger.info("Sound: silent mode")

    def start(self):
        self.bus.subscribe("pet/sound/play", self._on_play_sound)
        self.bus.subscribe("pet/emotion/changed", self._on_emotion_changed)
        if self._sound_thread is None or not self._sound_thread.is_alive():
            self._mixer_ready.clear()
            self._sound_thread = threading.Thread(target=self._sound_loop, daemon=True)
            self._sound_thread.start()
            self._mixer_ready.wait(timeout=2)
        logger.info("Sound: ready")

    def stop(self):
        self._running = False
        try:
            self._play_queue.put_nowait(None)
        except queue.Full:
            try:
                self._play_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._play_queue.put_nowait(None)
            except queue.Full:
                pass
        if self._sound_thread is not None:
            self._sound_thread.join(timeout=2)
            self._sound_thread = None

    def _on_play_sound(self, topic, data):
        data = unwrap_event_payload(data)
        name = data.get("name")
        if name in self.sounds:
            self._play_sound(name)
        else:
            logger.debug(f"Unknown sound: {name}")

    def _on_emotion_changed(self, topic, data):
        data = unwrap_event_payload(data)
        sound = data.get("sound")
        if sound and sound in self.sounds:
            self.bus.publish("pet/sound/play", {"name": sound, "source": "emotion"})
        # Also log to HAL for tracking
        self.hal.play_sound(sound)

    def _play_sound(self, name):
        """Generate and play sound in a separate thread to avoid blocking."""
        self._play_queue.put_nowait(name)

    def _sound_loop(self):
        try:
            if self.audio_enabled:
                try:
                    pygame.mixer.init(frequency=22050, size=-16, channels=1)
                    self._preload_sounds()
                    logger.info("Sound: audio ready")
                except Exception as exc:
                    self.audio_enabled = False
                    self._sound_cache = {}
                    logger.info("Sound: silent mode (%s)", exc)
            self._mixer_ready.set()

            while self._running:
                try:
                    name = self._play_queue.get(timeout=0.1)
                except queue.Empty:
                    continue
                if name is None:
                    break
                if not self.audio_enabled:
                    continue
                sound = self._sound_cache.get(name, self._sound_cache.get("neutral"))
                if sound is None:
                    logger.debug(f"No cached sound available for {name}")
                    continue
                try:
                    sound.play()
                except Exception as exc:
                    logger.error(f"Error playing sound {name}: {exc}")
        finally:
            if SOUND_AVAILABLE and pygame.mixer.get_init():
                try:
                    pygame.mixer.quit()
                except Exception:
                    pass

    def _preload_sounds(self):
        for name, generator in self.sounds.items():
            try:
                samples = generator()
                samples = (samples * 32767).astype(np.int16)
                self._sound_cache[name] = pygame.sndarray.make_sound(samples)
            except Exception as e:
                logger.warning(f"Failed to cache sound {name}: {e}")
        if "neutral" not in self._sound_cache and "boop" in self._sound_cache:
            self._sound_cache["neutral"] = self._sound_cache["boop"]

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
