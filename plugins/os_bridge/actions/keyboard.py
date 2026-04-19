import logging
import platform
import subprocess
import time

logger = logging.getLogger(__name__)

HOTKEY_INTERVAL = 0.03
ACTION_PAUSE = 0.03
SAVE_DIALOG_DELAY = 0.75

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


def _validate_key(pag, key: str) -> str:
    value = str(key).strip().lower()
    if not value:
        raise ValueError("key name must be non-empty")
    aliases = {
        "cmd": "command",
    }
    value = aliases.get(value, value)
    keyboard_keys = getattr(pag, "KEYBOARD_KEYS", None)
    if isinstance(keyboard_keys, (list, tuple, set)) and value not in keyboard_keys:
        raise ValueError(f"invalid pyautogui key: {key!r}")
    return value


def _normalize_hotkey_keys(pag, keys) -> list[str]:
    cleaned: list[str] = []
    for key in keys:
        value = str(key).strip()
        if not value:
            continue
        parts = [part.strip() for part in value.replace("+", " ").split() if part.strip()]
        if not parts:
            continue
        cleaned.extend(_validate_key(pag, part) for part in parts)
    return cleaned


def _copy_to_clipboard(text: str) -> bool:
    if platform.system().lower() == "darwin":
        try:
            subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True, timeout=5)
            return True
        except Exception as exc:
            logger.debug("pbcopy unavailable: %s", exc)
    if platform.system().lower() == "windows":
        try:
            import win32clipboard  # type: ignore

            win32clipboard.OpenClipboard()
            try:
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardText(text, win32clipboard.CF_UNICODETEXT)
            finally:
                win32clipboard.CloseClipboard()
            return True
        except Exception as exc:
            logger.debug("win32clipboard unavailable: %s", exc)
        try:
            import ctypes

            from ctypes import wintypes

            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            if not user32.OpenClipboard(None):
                raise RuntimeError("OpenClipboard failed")
            try:
                user32.EmptyClipboard()
                GMEM_MOVEABLE = 0x0002
                CF_UNICODETEXT = 13
                data = text.encode("utf-16-le") + b"\x00\x00"
                h_global = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
                if not h_global:
                    raise RuntimeError("GlobalAlloc failed")
                ptr = kernel32.GlobalLock(h_global)
                if not ptr:
                    raise RuntimeError("GlobalLock failed")
                try:
                    ctypes.memmove(ptr, data, len(data))
                finally:
                    kernel32.GlobalUnlock(h_global)
                if not user32.SetClipboardData(CF_UNICODETEXT, h_global):
                    raise RuntimeError("SetClipboardData failed")
                return True
            finally:
                user32.CloseClipboard()
        except Exception as exc:
            logger.debug("ctypes clipboard fallback unavailable: %s", exc)

    try:
        import tkinter as tk

        root = tk.Tk()
        root.withdraw()
        root.clipboard_clear()
        root.clipboard_append(text)
        root.update()
        root.destroy()
        return True
    except Exception as exc:
        logger.debug("clipboard fallback unavailable: %s", exc)
        return False


def _clear_clipboard() -> None:
    if platform.system().lower() == "windows":
        try:
            import win32clipboard  # type: ignore

            win32clipboard.OpenClipboard()
            try:
                win32clipboard.EmptyClipboard()
            finally:
                win32clipboard.CloseClipboard()
            return
        except Exception:
            pass
    try:
        import tkinter as tk

        root = tk.Tk()
        root.withdraw()
        root.clipboard_clear()
        root.update()
        root.destroy()
    except Exception:
        pass


def type_text(text: str) -> None:
    pag = _require_pyautogui()
    value = str(text)
    logger.info("[OS] typing text")
    _pause(ACTION_PAUSE)
    if any(ord(ch) > 127 for ch in value) or len(value) > 50:
        # pyautogui.write is unreliable for non-ASCII text on several platforms,
        # so paste from the clipboard instead when Unicode is present.
        if _copy_to_clipboard(value):
            paste_hotkey = ("command", "v") if platform.system().lower() == "darwin" else ("ctrl", "v")
            pag.hotkey(*paste_hotkey, interval=HOTKEY_INTERVAL)
            time.sleep(0.1)
            _clear_clipboard()
            _pause(ACTION_PAUSE)
            return
        logger.debug("clipboard fallback unavailable; using direct key injection")
    pag.write(value, interval=0.01)
    _pause(ACTION_PAUSE)


def press(key: str) -> None:
    pag = _require_pyautogui()
    value = _validate_key(pag, key)
    logger.info("[OS] pressing %s", value)
    _pause(ACTION_PAUSE)
    pag.press(value)
    _pause(ACTION_PAUSE)


def hotkey(*keys) -> None:
    pag = _require_pyautogui()
    cleaned = _normalize_hotkey_keys(pag, keys)
    if not cleaned:
        raise ValueError("hotkey requires at least one key")
    logger.info("[OS] hotkey %s", "+".join(cleaned))
    _pause(ACTION_PAUSE)
    pag.hotkey(*cleaned, interval=HOTKEY_INTERVAL)
    _pause(ACTION_PAUSE)


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
    _pause(SAVE_DIALOG_DELAY)
    pag.write(value, interval=0.01)
    _pause(0.15)
    pag.press("enter")
    _pause(0.1)
