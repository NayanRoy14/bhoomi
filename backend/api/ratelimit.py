"""Per-client rate limiting (PLAN.md 8).

Search is cheap for Bhoomi but not free for the upstream catalogue: every call
becomes a request to Earth Search. An unthrottled public endpoint turns Bhoomi
into an amplifier pointed at somebody else's service, which is a good way to get
the deployment's IP blocked.

In-memory now, matching the `cache.py` pattern: a Protocol with a simple backend,
so a Redis implementation drops in for multi-worker deployments without changing
any call site. A single process behind one uvicorn is the December shape.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import defaultdict, deque
from typing import Protocol, runtime_checkable

from fastapi import Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

#: Generous for a human drawing polygons; bounds a script. Jobs get a much
#: tighter budget (20/hour) when they exist in January.
SEARCH_LIMIT = int(os.getenv("BHOOMI_SEARCH_LIMIT", "120"))
WINDOW_SECONDS = int(os.getenv("BHOOMI_RATE_WINDOW", "3600"))

#: Number of trusted reverse proxies in front of the app.
#:
#: X-Forwarded-For is client-supplied and trivially spoofed, so honouring it
#: blindly would let anyone reset their own limit with a header. Only the
#: rightmost `hops` entries were appended by infrastructure we control. Default
#: 0 means "no proxy": use the socket peer and ignore the header entirely.
TRUSTED_PROXY_HOPS = int(os.getenv("BHOOMI_TRUSTED_PROXY_HOPS", "0"))


@runtime_checkable
class RateLimiter(Protocol):
    def check(self, key: str) -> tuple[bool, int]:
        """Return (allowed, retry_after_seconds). Records the hit when allowed."""

    def reset(self) -> None:
        ...


class SlidingWindowLimiter(RateLimiter):
    """Sliding window log.

    A fixed window would let a caller send 2x the limit across a boundary --
    all of it in the last second of one window and the first of the next. The
    log costs at most `limit` timestamps per key, which is small enough here.
    """

    def __init__(self, limit: int = SEARCH_LIMIT, window: int = WINDOW_SECONDS) -> None:
        self.limit = limit
        self.window = window
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()
        self._last_prune = 0.0

    def check(self, key: str) -> tuple[bool, int]:
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            hits = self._hits[key]
            while hits and hits[0] <= now - self.window:
                hits.popleft()

            if len(hits) >= self.limit:
                retry_after = max(1, int(hits[0] + self.window - now) + 1)
                return False, retry_after

            hits.append(now)
            return True, 0

    def _prune(self, now: float) -> None:
        """Drop keys with no recent hits, so idle clients do not accumulate.

        Without this the dict grows once per distinct IP, forever.
        """
        if now - self._last_prune < self.window:
            return
        self._last_prune = now
        cutoff = now - self.window
        stale = [k for k, hits in self._hits.items() if not hits or hits[-1] <= cutoff]
        for key in stale:
            del self._hits[key]
        if stale:
            logger.debug("rate limiter: pruned %d idle keys", len(stale))

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()
            self._last_prune = 0.0


def client_key(request: Request) -> str:
    """Identify the caller for rate-limiting purposes.

    Never trusts X-Forwarded-For unless TRUSTED_PROXY_HOPS says a proxy of ours
    put it there. Anything else is a header the client chose.
    """
    if TRUSTED_PROXY_HOPS > 0:
        forwarded = request.headers.get("x-forwarded-for", "")
        chain = [part.strip() for part in forwarded.split(",") if part.strip()]
        if len(chain) >= TRUSTED_PROXY_HOPS:
            return chain[-TRUSTED_PROXY_HOPS]
    return request.client.host if request.client else "unknown"


#: Jobs are real compute, not a proxied query, so they get their own much
#: tighter budget (PLAN.md 8) checked inside the route rather than in the
#: middleware -- the middleware sees every path and cannot tell a submission
#: from a status poll, and a client polling its own job at 2 s (7.4) must not
#: burn its job budget doing so.
JOB_LIMIT = int(os.getenv("BHOOMI_JOB_LIMIT", "20"))

_limiter: RateLimiter = SlidingWindowLimiter()
_job_limiter: RateLimiter = SlidingWindowLimiter(limit=JOB_LIMIT)


def set_job_limiter(limiter: RateLimiter) -> None:
    global _job_limiter
    _job_limiter = limiter


def get_job_limiter() -> RateLimiter:
    return _job_limiter

#: Monitoring must never be throttled -- a health check that starts returning
#: 429 reads as an outage and orchestrators restart containers over it.
EXEMPT_PATHS = frozenset({"/health", "/docs", "/openapi.json", "/redoc"})


def set_limiter(limiter: RateLimiter) -> None:
    """Replace the backend. Used by tests and by the Redis bootstrap."""
    global _limiter
    _limiter = limiter


def get_limiter() -> RateLimiter:
    return _limiter


async def rate_limit_middleware(request: Request, call_next):
    if request.url.path in EXEMPT_PATHS or request.method == "OPTIONS":
        return await call_next(request)

    key = client_key(request)
    allowed, retry_after = _limiter.check(key)
    if not allowed:
        logger.warning("rate limit hit by %s on %s", key, request.url.path)
        return JSONResponse(
            status_code=429,
            headers={"Retry-After": str(retry_after)},
            content={
                "code": "rate_limited",
                "message": (
                    f"Too many requests. The limit is {SEARCH_LIMIT} per hour. "
                    f"Try again in {retry_after} seconds."
                ),
                "retry_after": retry_after,
            },
        )
    return await call_next(request)
