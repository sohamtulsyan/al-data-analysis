"""Global token-bucket rate limiter (PRD §6.1)."""

from __future__ import annotations

import threading
import time


class TokenBucketRateLimiter:
    """Process-wide, thread-safe token-bucket limiter for every outbound HTTP request.

    Refill rate = RATE_LIMIT_RPS tokens/second. Capacity is max(1, RATE_LIMIT_RPS)
    so sub-1 RPS rates (e.g. 0.5 ≈ 30 req/min) can still issue one request at a time.
    Each request acquires one token before send; blocks if empty.
    """

    def __init__(self, rate_limit_rps: float) -> None:
        if rate_limit_rps <= 0:
            raise ValueError("rate_limit_rps must be > 0")
        self._refill_rate = float(rate_limit_rps)
        # At least one token of capacity so a single request can proceed under sub-1 RPS
        self._capacity = max(1.0, float(rate_limit_rps))
        self._tokens = self._capacity
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        """Block until one token is available, then consume it."""
        while True:
            with self._lock:
                now = time.monotonic()
                elapsed = now - self._last_refill
                if elapsed > 0:
                    self._tokens = min(
                        self._capacity,
                        self._tokens + elapsed * self._refill_rate,
                    )
                    self._last_refill = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                # Seconds until one token is available
                wait = (1.0 - self._tokens) / self._refill_rate
            time.sleep(max(wait, 0.001))
