"""In-process sliding-window rate limiting.

Deliberately simple and dependency-free so the dev stack needs no Redis.  For a
multi-worker deployment, move the counters to Redis - the call sites do not change.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from typing import Callable

from fastapi import Request

from app.core.config import settings
from app.core.errors import RateLimitError

_lock = threading.Lock()
_buckets: dict[str, deque[float]] = defaultdict(deque)

_UNITS = {"second": 1.0, "minute": 60.0, "hour": 3600.0, "day": 86400.0}


def parse_rule(rule: str) -> tuple[int, float]:
    """``"10/minute"`` -> ``(10, 60.0)``."""
    try:
        count_raw, _, window_raw = rule.partition("/")
        count = int(str(count_raw).strip())
        unit = str(window_raw).strip().lower().rstrip("s")
        window = _UNITS.get(unit, 60.0)
    except (TypeError, ValueError):
        count, window = 60, 60.0
    return max(count, 1), float(window)


def client_key(request: Request, scope: str) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    ip = (
        forwarded.split(",")[0].strip()
        if forwarded
        else (request.client.host if request.client else "unknown")
    )
    user_id = getattr(request.state, "user_id", None)
    return f"{scope}:{user_id or ip}"


def hit(key: str, limit: int, window: float) -> tuple[bool, float]:
    now = time.monotonic()
    with _lock:
        bucket = _buckets[key]
        while bucket and now - bucket[0] > window:
            bucket.popleft()
        if len(bucket) >= limit:
            retry = window - (now - bucket[0])
            return False, max(retry, 1.0)
        bucket.append(now)
        return True, 0.0


def limiter(scope: str, rule: str | None = None) -> Callable[[Request], None]:
    """FastAPI dependency enforcing ``rule`` (falls back to the global default)."""

    def dependency(request: Request) -> None:
        if not settings.rate_limit_enabled:
            return
        limit, window = parse_rule(rule or settings.rate_limit_default)
        allowed, retry = hit(client_key(request, scope), limit, window)
        if not allowed:
            raise RateLimitError(
                f"Too many requests for this operation ({limit} per {window:.0f}s). "
                f"Please retry in {retry:.0f}s.",
                details={
                    "retry_after_seconds": round(retry, 1),
                    "limit": limit,
                    "window_seconds": window,
                },
            )

    return dependency


def reset_state() -> None:
    """Test helper."""
    with _lock:
        _buckets.clear()
