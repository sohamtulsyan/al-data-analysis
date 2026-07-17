"""Transient 429 / 5xx retry with exponential backoff + jitter (PRD §6.4)."""

from __future__ import annotations

import random
import time
from typing import Callable, Optional, TypeVar

import requests

from retention_pipeline.api.rate_limiter import TokenBucketRateLimiter
from retention_pipeline.config import (
    RETRY_BACKOFF_BASE_SECONDS,
    RETRY_BACKOFF_CAP_SECONDS,
    RETRY_MAX_ATTEMPTS,
)

T = TypeVar("T")


class TransientHTTPError(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        super().__init__(message)


def retry_transient(
    make_request: Callable[[], requests.Response],
    *,
    rate_limiter: Optional[TokenBucketRateLimiter] = None,
    max_attempts: int = RETRY_MAX_ATTEMPTS,
    base_seconds: float = RETRY_BACKOFF_BASE_SECONDS,
    cap_seconds: float = RETRY_BACKOFF_CAP_SECONDS,
) -> requests.Response:
    """Retry the same request on 429 / 5xx with doubling delay, capped, + jitter."""
    last_exc: Optional[Exception] = None
    for attempt in range(max_attempts):
        if rate_limiter is not None and attempt > 0:
            rate_limiter.acquire()
        try:
            response = make_request()
        except requests.RequestException as exc:
            last_exc = exc
            if attempt + 1 >= max_attempts:
                raise
            _sleep_backoff(attempt, base_seconds, cap_seconds)
            continue

        if response.status_code == 429 or response.status_code >= 500:
            last_exc = TransientHTTPError(
                response.status_code,
                f"HTTP {response.status_code}: {response.text[:300]}",
            )
            if attempt + 1 >= max_attempts:
                raise last_exc
            _sleep_backoff(attempt, base_seconds, cap_seconds)
            continue

        return response

    assert last_exc is not None
    raise last_exc


def _sleep_backoff(attempt: int, base: float, cap: float) -> None:
    delay = min(cap, base * (2**attempt))
    jitter = random.uniform(0, delay * 0.25)
    time.sleep(delay + jitter)
