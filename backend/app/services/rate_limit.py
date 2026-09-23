"""
In-process sliding-window rate limiter for the auth endpoints.

Login and registration are the brute-force surface: without a limit, anyone
can guess passwords as fast as the server hashes them. This keeps a short
per-key history of attempt timestamps and refuses once a key exceeds its
budget inside the window.

Scope: one backend process. Behind several workers or replicas each keeps its
own counts, so the effective limit multiplies — move this to Redis (or the
edge proxy) when the backend scales out. Client IPs come from the direct peer;
behind a reverse proxy configure uvicorn's --proxy-headers so that is the
real client.
"""
from __future__ import annotations

import threading
import time
from collections import deque


class RateLimiter:
    def __init__(self, max_attempts: int, window_seconds: float) -> None:
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def hit(self, key: str) -> bool:
        """Record an attempt for `key`. False if it is over the limit."""
        now = time.monotonic()
        cutoff = now - self.window_seconds
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and hits[0] < cutoff:
                hits.popleft()
            if len(hits) >= self.max_attempts:
                return False
            hits.append(now)
            # Keep memory bounded: drop keys whose history has fully expired.
            if len(self._hits) > 10_000:
                for k in [k for k, v in self._hits.items() if not v or v[-1] < cutoff]:
                    del self._hits[k]
            return True


class AuthRateLimits:
    """The limits applied to /auth routes (one instance per app)."""

    def __init__(self) -> None:
        # Per client IP across login + register: stops one host spraying.
        self.per_ip = RateLimiter(max_attempts=30, window_seconds=300)
        # Per target account: stops a distributed guess on one email.
        self.per_email = RateLimiter(max_attempts=10, window_seconds=900)
