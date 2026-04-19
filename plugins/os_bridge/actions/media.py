from __future__ import annotations

import logging
import platform
import subprocess

from .apps import open_url

logger = logging.getLogger(__name__)

IS_MACOS = platform.system().lower() == "darwin"
IS_WINDOWS = platform.system().lower() == "windows"


def _press_media_key(key: str) -> None:
    try:
        import pyautogui

        pyautogui.press(key)
    except Exception as exc:
        raise RuntimeError(f"media key unavailable: {exc}") from exc


def play_pause_media() -> None:
    _press_media_key("playpause")


def next_track() -> None:
    _press_media_key("nexttrack")


def previous_track() -> None:
    _press_media_key("prevtrack")


def stop_media() -> None:
    _press_media_key("stop")


def spotify_play(song: str = None, artist: str = None) -> None:
    if not IS_MACOS:
        open_url("https://open.spotify.com")
        return
    query = " ".join(part for part in [song or "", artist or ""] if part).strip()
    script = [
        'tell application "Spotify"',
        "launch",
        "activate",
    ]
    if query:
        script.append(f'search "{query}"')
    script.append("end tell")
    subprocess.run(["osascript", "-e", "\n".join(script)], check=False, timeout=15)


def spotify_pause() -> None:
    if IS_MACOS:
        subprocess.run(["osascript", "-e", 'tell application "Spotify" to pause'], check=False, timeout=10)


def spotify_next() -> None:
    if IS_MACOS:
        subprocess.run(["osascript", "-e", 'tell application "Spotify" to next track'], check=False, timeout=10)


def spotify_get_current_song() -> dict:
    if not IS_MACOS:
        return {"name": "", "artist": "", "album": ""}
    try:
        result = subprocess.run(
            [
                "osascript",
                "-e",
                'tell application "Spotify" to get {name, artist, album} of current track',
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        parts = [part.strip() for part in result.stdout.split(",")]
        while len(parts) < 3:
            parts.append("")
        return {"name": parts[0], "artist": parts[1], "album": parts[2]}
    except Exception:
        return {"name": "", "artist": "", "album": ""}


def youtube_search_and_play(query: str) -> None:
    open_url(f"https://www.youtube.com/results?search_query={query}")
