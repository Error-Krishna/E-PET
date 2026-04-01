import logging

logger = logging.getLogger(__name__)

class HALSimulator:
    """Simulated hardware abstraction layer. No real hardware calls."""
    def __init__(self, debug: bool = False):
        self.debug = debug
        self._face = "neutral"
        self._led_color = "off"
        self._led_mode = "static"

    def set_face(self, expression: str) -> None:
        self._face = expression
        if self.debug:
            logger.debug(f"HAL: set face to {expression}")

    def play_sound(self, name: str) -> None:
        if self.debug:
            logger.debug(f"HAL: play sound {name}")

    def set_led(self, color: str, mode: str) -> None:
        self._led_color = color
        self._led_mode = mode
        if self.debug:
            logger.debug(f"HAL: set LED to color={color}, mode={mode}")

    def get_state(self) -> dict:
        """For testing purposes only."""
        return {
            "face": self._face,
            "led_color": self._led_color,
            "led_mode": self._led_mode,
        }