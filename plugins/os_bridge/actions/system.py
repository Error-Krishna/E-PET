from __future__ import annotations

import ctypes
import datetime as _dt
import logging
import os
import platform
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path

logger = logging.getLogger(__name__)

IS_MACOS = platform.system().lower() == "darwin"
IS_WINDOWS = platform.system().lower() == "windows"
IS_LINUX = platform.system().lower().startswith("linux")

try:
    import psutil

    PSUTIL_AVAILABLE = True
except Exception:
    psutil = None
    PSUTIL_AVAILABLE = False


def _run(cmd, **kwargs):
    return subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=kwargs.pop("timeout", 10), **kwargs)


def get_volume() -> int:
    if IS_MACOS:
        try:
            result = _run(["osascript", "-e", "output volume of (get volume settings)"])
            return max(0, min(100, int(result.stdout.strip())))
        except Exception:
            return 50
    if IS_WINDOWS and PSUTIL_AVAILABLE:
        return 50
    return 50


def set_volume(level: int) -> None:
    level = max(0, min(100, int(level)))
    if IS_MACOS:
        try:
            _run(["osascript", "-e", f"set volume output volume {level}"], timeout=5)
            return
        except subprocess.TimeoutExpired:
            raise RuntimeError("Volume command timed out")
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Volume command failed: {e.stderr}")
    if IS_WINDOWS:
        try:
            _run(
                [
                    "powershell",
                    "-Command",
                    f"(New-Object -ComObject WScript.Shell).SendKeys('{level}')",
                ],
                timeout=5,
            )
            return
        except Exception as e:
            raise RuntimeError(f"Volume control failed on Windows: {e}")
    if IS_LINUX:
        try:
            _run(["amixer", "sset", "Master", f"{level}%"], timeout=5)
            return
        except FileNotFoundError:
            _run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{level}%"], timeout=5)
            return


def mute() -> None:
    if IS_MACOS:
        _run(["osascript", "-e", "set volume with output muted"], timeout=5)
    elif IS_WINDOWS:
        _run(["powershell", "-Command", "(New-Object -ComObject WScript.Shell).SendKeys([char]173)"], timeout=5)
    else:
        _run(["amixer", "set", "Master", "mute"], timeout=5)


def unmute() -> None:
    if IS_MACOS:
        _run(["osascript", "-e", "set volume without output muted"], timeout=5)
    elif IS_WINDOWS:
        _run(["powershell", "-Command", "(New-Object -ComObject WScript.Shell).SendKeys([char]173)"], timeout=5)
    else:
        _run(["amixer", "set", "Master", "unmute"], timeout=5)


def toggle_mute() -> None:
    mute()


def get_brightness() -> int:
    return 50


def set_brightness(level: int) -> None:
    level = max(0, min(100, int(level)))
    if IS_MACOS:
        try:
            _run(["osascript", "-e", f"set brightness to {level / 100:.2f}"], timeout=5)
            return
        except Exception as exc:
            raise RuntimeError(f"brightness control failed: {exc}") from exc
    if IS_WINDOWS:
        try:
            _run(
                [
                    "powershell",
                    "-Command",
                    f"(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods).WmiSetBrightness(1,{level})",
                ],
                timeout=5,
            )
            return
        except Exception as exc:
            raise RuntimeError(f"brightness control failed on Windows: {exc}") from exc


def lock_screen() -> None:
    if IS_MACOS:
        _run(["pmset", "displaysleepnow"], timeout=5)
        return
    if IS_WINDOWS:
        ctypes.windll.user32.LockWorkStation()
        return
    _run(["loginctl", "lock-session"], timeout=5)


def sleep_system() -> None:
    if IS_MACOS:
        _run(["pmset", "sleepnow"], timeout=5)
        return
    if IS_WINDOWS:
        _run(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"], timeout=5)
        return
    _run(["systemctl", "suspend"], timeout=5)


def restart_system(delay_seconds: int = 0) -> None:
    delay_seconds = max(0, int(delay_seconds))
    if IS_WINDOWS:
        _run(["shutdown", "/r", "/t", str(delay_seconds)], timeout=5)
        return
    _run(["shutdown", "-r", f"+{delay_seconds}"], timeout=5)


def shutdown_system(delay_seconds: int = 0) -> None:
    delay_seconds = max(0, int(delay_seconds))
    if IS_WINDOWS:
        _run(["shutdown", "/s", "/t", str(delay_seconds)], timeout=5)
        return
    _run(["shutdown", "-h", f"+{delay_seconds}"], timeout=5)


def get_battery_status() -> dict:
    if IS_MACOS:
        try:
            result = _run(["pmset", "-g", "batt"], timeout=5)
            output = result.stdout.lower()
            percent = 0
            charging = "charging" in output
            for token in output.replace(";", " ").split():
                if token.endswith("%"):
                    percent = int(token.strip("%"))
                    break
            return {"percent": percent, "charging": charging, "time_remaining": ""}
        except Exception:
            return {"percent": 0, "charging": False, "time_remaining": ""}
    if PSUTIL_AVAILABLE:
        battery = psutil.sensors_battery()
        if battery:
            return {
                "percent": int(battery.percent),
                "charging": bool(battery.power_plugged),
                "time_remaining": battery.secsleft,
            }
    return {"percent": 0, "charging": False, "time_remaining": ""}


def get_wifi_name() -> str:
    if IS_MACOS:
        try:
            result = _run(["networksetup", "-getairportnetwork", "en0"], timeout=5)
            return result.stdout.split(":", 1)[-1].strip()
        except Exception:
            return ""
    return ""


def get_ip_address() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except Exception:
        return "127.0.0.1"


def check_internet_connection() -> bool:
    cmd = ["ping", "-c", "1", "8.8.8.8"] if not IS_WINDOWS else ["ping", "-n", "1", "8.8.8.8"]
    try:
        _run(cmd, timeout=5)
        return True
    except Exception:
        return False


def get_running_processes() -> list[dict]:
    if PSUTIL_AVAILABLE:
        items = []
        for proc in psutil.process_iter(attrs=["pid", "name", "cpu_percent", "memory_percent"]):
            info = proc.info
            items.append(
                {
                    "name": info.get("name", ""),
                    "pid": info.get("pid"),
                    "cpu": info.get("cpu_percent", 0.0),
                    "memory": info.get("memory_percent", 0.0),
                }
            )
        return items
    return []


def kill_process(name_or_pid: str | int) -> None:
    if PSUTIL_AVAILABLE:
        try:
            pid = int(name_or_pid)
            psutil.Process(pid).kill()
            return
        except Exception:
            pass
        for proc in psutil.process_iter(attrs=["pid", "name"]):
            if str(proc.info.get("name", "")).lower() == str(name_or_pid).lower():
                proc.kill()
                return
    raise RuntimeError(f"unable to kill process: {name_or_pid}")


def get_disk_usage(path: str = "/") -> dict:
    usage = shutil.disk_usage(path)
    percent = (usage.used / usage.total * 100.0) if usage.total else 0.0
    return {"total": usage.total, "used": usage.used, "free": usage.free, "percent": percent}


def get_memory_usage() -> dict:
    if PSUTIL_AVAILABLE:
        vm = psutil.virtual_memory()
        return {"total": vm.total, "used": vm.used, "available": vm.available, "percent": vm.percent}
    return {"total": 0, "used": 0, "available": 0, "percent": 0}


def get_cpu_usage() -> float:
    if PSUTIL_AVAILABLE:
        return float(psutil.cpu_percent(interval=0.1))
    return 0.0


def empty_trash() -> None:
    if IS_MACOS:
        _run(["osascript", "-e", 'tell application "Finder" to empty trash'], timeout=10)
        return
    if IS_WINDOWS:
        try:
            import winshell  # type: ignore

            winshell.recycle_bin().empty(confirm=False)
            return
        except Exception as exc:
            raise RuntimeError(f"unable to empty trash on Windows: {exc}") from exc
    trash = Path.home() / ".local/share/Trash"
    if trash.exists():
        shutil.rmtree(trash, ignore_errors=True)


def set_wallpaper(image_path: str) -> None:
    path = str(Path(image_path).expanduser())
    if IS_MACOS:
        _run(["osascript", "-e", f'tell application "System Events" to set picture of every desktop to "{path}"'], timeout=10)
        return
    if IS_WINDOWS:
        ctypes.windll.user32.SystemParametersInfoW(20, 0, path, 0)
        return


def get_current_time() -> str:
    return _dt.datetime.now().strftime("%H:%M:%S")


def get_current_date() -> str:
    return _dt.datetime.now().strftime("%Y-%m-%d")


def set_system_time_zone(tz: str) -> None:
    if IS_WINDOWS:
        _run(["tzutil", "/s", tz], timeout=5)
        return
    _run(["sudo", "timedatectl", "set-timezone", tz], timeout=10)


def take_screenshot(path: str = None, region: tuple = None) -> str:
    from .screen import screenshot as capture

    image = capture(region=region)
    output = Path(path).expanduser() if path else Path(tempfile.gettempdir()) / f"epet-screenshot-{int(time.time())}.png"
    image.save(output)
    return str(output)


def record_screen(duration: int, path: str = None) -> str:
    output = Path(path).expanduser() if path else Path(tempfile.gettempdir()) / f"epet-recording-{int(time.time())}.mp4"
    try:
        _run(["ffmpeg", "-y", "-t", str(int(duration)), "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=10", str(output)], timeout=max(10, int(duration) + 5))
    except Exception:
        take_screenshot(str(output.with_suffix(".png")))
    return str(output)
