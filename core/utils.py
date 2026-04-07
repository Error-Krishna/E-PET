import time
import functools


def profile(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        start = time.perf_counter()
        result = func(*args, **kwargs)
        elapsed = time.perf_counter() - start
        print(f"[PROFILE] {func.__qualname__} took {elapsed:.3f}s")
        return result
    return wrapper
