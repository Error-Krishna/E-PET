import functools
import os
import time


def _profile_enabled() -> bool:
    return os.environ.get("EPET_PROFILE", "").strip().lower() in {"1", "true", "yes", "on"}


def profile(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        start = time.perf_counter()
        result = func(*args, **kwargs)
        elapsed = time.perf_counter() - start
        if _profile_enabled():
            print(f"[PROFILE] {func.__qualname__} took {elapsed:.3f}s")
        return result
    return wrapper


def unwrap_event_payload(data):
    """Return the inner payload for ordered bus events, otherwise the data as-is."""
    if isinstance(data, dict):
        if {"seq", "timestamp", "topic"}.issubset(data.keys()) and isinstance(data.get("data"), dict):
            return data["data"]
    return data
