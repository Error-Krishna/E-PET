import logging
import platform
import shutil
import subprocess

logger = logging.getLogger(__name__)


def open_app(name: str) -> None:
    app_name = str(name).strip()
    if not app_name:
        raise ValueError("open_app requires a non-empty app name")

    system = platform.system().lower()
    try:
        if system == "windows":
            logger.info("[OS] executing open_app %s", app_name)
            process = subprocess.Popen(["start", app_name], shell=True)
            return_code = process.wait(timeout=10)
            if return_code not in (0, None):
                raise RuntimeError(f"launch failed with exit code {return_code}")
            return

        if system == "darwin":
            logger.info("[OS] executing open_app %s", app_name)
            process = subprocess.Popen(
                ["open", "-a", app_name],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            stdout, stderr = process.communicate(timeout=10)
            if process.returncode not in (0, None):
                message = stderr.decode("utf-8", errors="ignore").strip() or stdout.decode("utf-8", errors="ignore").strip()
                raise RuntimeError(message or "launch failed")
            return

        if system == "linux":
            if shutil.which(app_name) is None and not any(sep in app_name for sep in ("/", "\\")):
                raise FileNotFoundError(f"app not found: {app_name}")
            logger.info("[OS] executing open_app %s", app_name)
            subprocess.Popen([app_name], start_new_session=True)
            return

        raise RuntimeError(f"unsupported platform: {system}")
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"app not found: {app_name}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"timed out launching app: {app_name}") from exc
    except Exception as exc:
        raise RuntimeError(f"failed to launch app '{app_name}': {exc}") from exc
