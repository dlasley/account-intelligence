import time
from collections import deque
from threading import Lock

_buckets: dict[str, deque[float]] = {}
_lock = Lock()


def check_rate_limit(key_prefix: str, limit_per_minute: int) -> bool:
    """Returns True if the request is allowed; False if rate-limited.

    In-memory sliding window per Cloud Run instance. State resets on cold start.
    """
    now = time.monotonic()
    cutoff = now - 60.0

    with _lock:
        bucket = _buckets.setdefault(key_prefix, deque())
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= limit_per_minute:
            return False
        bucket.append(now)
        return True


def _reset_for_tests() -> None:
    """Test helper — clear all in-memory buckets."""
    with _lock:
        _buckets.clear()
