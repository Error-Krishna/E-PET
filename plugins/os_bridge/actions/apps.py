import logging
import platform
import shutil
import subprocess
import time
import webbrowser

logger = logging.getLogger(__name__)

DEFAULT_BROWSER_TARGET = "__default_browser__"

APP_ALIASES = {
    "notepad": {
        "windows": ["notepad"],
        "darwin": ["TextEdit"],
        "linux": ["gedit", "xed", "kate"],
    },
    "textedit": {
        "windows": ["notepad"],
        "darwin": ["TextEdit"],
        "linux": ["gedit", "xed", "kate"],
    },
    "chrome": {
        "windows": ["chrome", "google chrome"],
        "darwin": ["Google Chrome", "Google Chrome Canary"],
        "linux": ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser"],
    },
    "google chrome": {
        "windows": ["chrome", "google chrome"],
        "darwin": ["Google Chrome", "Google Chrome Canary"],
        "linux": ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser"],
    },
    "default browser": {
        "*": [DEFAULT_BROWSER_TARGET],
    },
    "system default browser": {
        "*": [DEFAULT_BROWSER_TARGET],
    },
}


def _dedupe(values):
    seen = set()
    result = []
    for value in values:
        key = value.lower() if isinstance(value, str) else value
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def resolve_open_app_candidates(name: str):
    app_name = str(name).strip()
    if not app_name:
        return []

    system = platform.system().lower()
    normalized = app_name.lower()
    candidates = [app_name]

    alias_map = APP_ALIASES.get(normalized, {})
    if "chrome" in normalized:
        fallback_aliases = ["google chrome", DEFAULT_BROWSER_TARGET]
        if system == "darwin":
            fallback_aliases.extend(["Google Chrome", "Google Chrome Canary"])
        elif system == "windows":
            fallback_aliases.extend(["chrome", "google chrome"])
        else:
            fallback_aliases.extend(["google-chrome", "google-chrome-stable", "chromium", "chromium-browser"])

        for alias in fallback_aliases:
            if alias not in candidates:
                candidates.append(alias)
        candidates.extend(alias_map.get(system, []))
        candidates.extend(alias_map.get("*", []))
    else:
        candidates.extend(alias_map.get(system, []))
        candidates.extend(alias_map.get("*", []))

        if "browser" in normalized and DEFAULT_BROWSER_TARGET not in candidates:
            candidates.append(DEFAULT_BROWSER_TARGET)

    return _dedupe(candidates)


def open_app(name: str) -> None:
    app_name = str(name).strip()
    if not app_name:
        raise ValueError("open_app requires a non-empty app name")

    system = platform.system().lower()
    last_error = None
    for candidate in resolve_open_app_candidates(app_name):
        try:
            if candidate == DEFAULT_BROWSER_TARGET:
                logger.info("[OS] executing open_app default browser")
                if webbrowser.open_new_tab("about:blank"):
                    return
                raise RuntimeError("default browser launch failed")

            logger.info("[OS] executing open_app %s", candidate)
            if system == "windows":
                process = subprocess.Popen(["start", candidate], shell=True)
                return_code = process.wait(timeout=10)
                if return_code not in (0, None):
                    raise RuntimeError(f"launch failed with exit code {return_code}")
                return

            if system == "darwin":
                process = subprocess.Popen(
                    ["open", "-a", candidate],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                stdout, stderr = process.communicate(timeout=10)
                if process.returncode not in (0, None):
                    message = stderr.decode("utf-8", errors="ignore").strip() or stdout.decode("utf-8", errors="ignore").strip()
                    raise RuntimeError(message or "launch failed")
                return

            if system == "linux":
                if shutil.which(candidate) is None and not any(sep in candidate for sep in ("/", "\\")):
                    raise FileNotFoundError(f"app not found: {candidate}")
                subprocess.Popen([candidate], start_new_session=True)
                return

            raise RuntimeError(f"unsupported platform: {system}")
        except FileNotFoundError as exc:
            last_error = exc
        except subprocess.TimeoutExpired as exc:
            last_error = RuntimeError(f"timed out launching app: {candidate}")
        except Exception as exc:
            last_error = exc

    if isinstance(last_error, FileNotFoundError):
        raise FileNotFoundError(f"app not found: {app_name}") from last_error
    if last_error is not None:
        raise RuntimeError(f"failed to launch app '{app_name}': {last_error}") from last_error
    raise RuntimeError(f"failed to launch app '{app_name}'")


def open_url(url: str) -> None:
    target = str(url).strip()
    if not target:
        raise ValueError("open_url requires a non-empty url")

    logger.info("[OS] executing open_url %s", target)
    if webbrowser.open_new_tab(target):
        return

    try:
        open_app("chrome")
        time.sleep(0.5)
        if webbrowser.open_new_tab(target):
            return
    except Exception as exc:
        logger.debug("Fallback chrome launch failed for url %s: %s", target, exc)

    raise RuntimeError(f"failed to open url '{target}'")
