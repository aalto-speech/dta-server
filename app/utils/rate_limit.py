"""Request rate limits, per client IP and per user.

Per-IP limits are generous, since many learners can share one address. Per-user limits
run after the user is known to exist, so made-up guids add no counters.

Counters are in-memory fixed windows: per worker, reset on restart. Behind Caddy the
client IP comes from X-Forwarded-For, which needs FORWARDED_ALLOW_IPS (compose.yaml).
"""
import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from fastapi import Request

from app.config import SETTINGS
from app.error_handlers import AppError, ErrorType


@dataclass(frozen=True)
class RateLimit:
    """At most `limit` requests per `window_seconds` for one key."""

    name: str
    limit: int
    window_seconds: float


# Safety net on every route: 10 requests/s sustained for a minute.
GLOBAL_PER_IP = RateLimit("global_ip", limit=600, window_seconds=60)

# New accounts: room for a classroom, not for a script.
ONBOARDING_PER_IP = RateLimit("onboarding_ip", limit=60, window_seconds=3600)

# Recordings: up to 10 MB each, plus serialised GPU time.
SPEECH_PER_IP = RateLimit("speech_ip", limit=120, window_seconds=600)
SPEECH_PER_USER = RateLimit("speech_user", limit=20, window_seconds=600)

# Well above normal use (feedback is sent per change, events in batches).
FEEDBACK_PER_USER = RateLimit("feedback_user", limit=120, window_seconds=600)
EVENTS_PER_USER = RateLimit("events_user", limit=120, window_seconds=600)
COMPARISON_PER_USER = RateLimit("comparison_user", limit=120, window_seconds=600)

# Cohort changes; frequent moves would probe other cohorts' rankings.
LEVEL_PER_USER = RateLimit("level_user", limit=20, window_seconds=3600)

# Maintainer-only; bounds delete-key guessing.
DELETE_PER_IP = RateLimit("delete_ip", limit=60, window_seconds=3600)


class RateLimiter:
    """Fixed-window request counters keyed by (rule, key).

    Up to twice the limit can pass across a window boundary; fine for stopping abuse.
    """

    # Drop expired windows every this many calls to bound memory.
    _SWEEP_EVERY = 1024

    def __init__(self, enabled: bool = True,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.enabled = enabled
        self._clock = clock
        self._lock = threading.Lock()
        # (rule name, key) -> [window end, request count]
        self._windows: dict[tuple[str, str], list[float]] = {}
        self._calls = 0

    def hit(self, rule: RateLimit, key: str) -> float | None:
        """Count one request against `rule` for `key`.

        Returns:
            float | None: None when the request is allowed. Otherwise the seconds until
                the window ends; a refused request is not counted.
        """

        if not self.enabled:
            return None

        now = self._clock()
        with self._lock:
            self._calls += 1
            if self._calls % self._SWEEP_EVERY == 0:
                self._sweep(now)

            slot = (rule.name, key)
            window = self._windows.get(slot)
            if window is None or window[0] <= now:
                self._windows[slot] = [now + rule.window_seconds, 1]
                return None

            if window[1] >= rule.limit:
                return window[0] - now

            window[1] += 1
            return None

    def reset(self) -> None:
        """Forget every counter."""

        with self._lock:
            self._windows.clear()
            self._calls = 0

    def _sweep(self, now: float) -> None:
        expired = [slot for slot, window in self._windows.items()
                   if window[0] <= now]
        for slot in expired:
            del self._windows[slot]


LIMITER = RateLimiter(enabled=SETTINGS.rate_limit_enabled)


def enforce(rule: RateLimit, key: str) -> None:
    """Count a request and raise 429 RATE_LIMITED when `key` is over `rule`."""

    retry_after = LIMITER.hit(rule, key)
    if retry_after is None:
        return

    seconds = max(1, math.ceil(retry_after))
    raise AppError(
        status_code=429,
        error_type=ErrorType.RATE_LIMITED,
        message="Too many requests. Please try again later.",
        extra={"retry_after_seconds": seconds},
        headers={"Retry-After": str(seconds)},
    )


def enforce_for_user(rule: RateLimit, guid: UUID) -> None:
    """Per-user limit. Call only after the user is known to exist."""

    enforce(rule, str(guid))


def client_ip(request: Request) -> str:
    """The client's address as uvicorn resolved it, proxy headers included."""

    return request.client.host if request.client else "unknown"


def per_ip(rule: RateLimit) -> Callable[[Request], None]:
    """Build a FastAPI dependency that applies `rule` to the client's IP address."""

    def _dependency(request: Request) -> None:
        enforce(rule, client_ip(request))

    return _dependency
