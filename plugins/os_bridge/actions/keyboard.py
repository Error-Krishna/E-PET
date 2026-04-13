import logging
import platform
import time

logger = logging.getLogger(__name__)

try:
    import pyautogui as _pyautogui

    _pyautogui.FAILSAFE = True
    PYAUTOGUI_AVAILABLE = True
except Exception as exc:  # pragma: no cover - import environment dependent
    _pyautogui = None
    _pyautogui_error = exc
    PYAUTOGUI_AVAILABLE = False


def _require_pyautogui():
    if _pyautogui is None:
        raise RuntimeError(f"pyautogui is not available: {_pyautogui_error}")
    return _pyautogui


def _pause(delay: float = 0.03) -> None:
    time.sleep(max(0.0, delay))


def type_text(text: str) -> None:
    pag = _require_pyautogui()
    value = str(text)
    logger.info("[OS] typing text")
    _pause(0.05)
    pag.write(value, interval=0.01)
    _pause(0.05)


def press(key: str) -> None:
    pag = _require_pyautogui()
    value = str(key).strip()
    if not value:
        raise ValueError("press requires a non-empty key")
    logger.info("[OS] pressing %s", value)
    _pause(0.03)
    pag.press(value)
    _pause(0.03)


def hotkey(*keys) -> None:
    pag = _require_pyautogui()
    cleaned = [str(key).strip() for key in keys if str(key).strip()]
    if not cleaned:
        raise ValueError("hotkey requires at least one key")
    logger.info("[OS] hotkey %s", "+".join(cleaned))
    _pause(0.03)
    pag.hotkey(*cleaned, interval=0.03)
    _pause(0.03)


def save_file(filename: str) -> None:
    pag = _require_pyautogui()
    value = str(filename).strip()
    if not value:
        raise ValueError("save_file requires a non-empty filename")

    system = platform.system().lower()
    logger.info("[OS] saving file as %s", value)
    if system == "darwin":
        hotkey("command", "s")
    else:
        hotkey("ctrl", "s")
    _pause(0.4)
    pag.write(value, interval=0.01)
    _pause(0.15)
    pag.press("enter")
    _pause(0.1)
