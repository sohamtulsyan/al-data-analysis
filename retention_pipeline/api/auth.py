"""Authentication — token acquisition (PRD §2) with single-flight refresh (§6.3)."""

from __future__ import annotations

import threading
from typing import Any, Optional

import requests

from retention_pipeline.api.rate_limiter import TokenBucketRateLimiter
from retention_pipeline.config import Config
from retention_pipeline.api.retry import retry_transient


class TokenError(Exception):
    """Raised when token acquisition fails after all retries (PRD §2.3)."""

    def __init__(self, error_code: Any, error_message: str) -> None:
        self.error_code = error_code
        self.error_message = error_message
        super().__init__(
            f"Token acquisition failed after 3 attempts. "
            f"errorCode={error_code!r}, errorMessage={error_message!r}"
        )


class TokenManager:
    """In-memory bearer token shared by all workers (never written to disk/logs).

    Refresh under 401/errorCode 98 uses a single-flight lock (PRD §6.3).
    """

    def __init__(self, config: Config, rate_limiter: TokenBucketRateLimiter) -> None:
        self._config = config
        self._rate_limiter = rate_limiter
        self._session = requests.Session()
        self._token: Optional[str] = None
        self._lock = threading.Lock()
        self._refresh_lock = threading.Lock()

    @property
    def access_token(self) -> str:
        with self._lock:
            if self._token is None:
                raise RuntimeError("Token not acquired; call acquire() first.")
            return self._token

    def acquire(self) -> str:
        """POST /token — up to 3 attempts total (PRD §2.3)."""
        last_error_code: Any = None
        last_error_message = "unknown"
        url = f"{self._config.API_BASE_URL}/token"
        body = {
            "client_id": self._config.CLIENT_ID,
            "client_secret": self._config.CLIENT_SECRET,
        }

        for attempt in range(3):
            self._rate_limiter.acquire()
            try:
                response = retry_transient(
                    lambda: self._session.post(
                        url,
                        data=body,
                        headers={
                            "Content-Type": "application/x-www-form-urlencoded",
                            "Accept": "application/json",
                        },
                        timeout=60,
                    ),
                    rate_limiter=self._rate_limiter,
                )
            except requests.RequestException as exc:
                last_error_code = "NETWORK"
                last_error_message = str(exc)
                continue

            try:
                data = response.json()
            except ValueError:
                data = {}

            # Success: HTTP 200, body status == 0, non-empty access_token (PRD §2.2)
            if response.status_code == 200 and data.get("status", 0) == 0:
                token = data.get("access_token")
                if token:
                    with self._lock:
                        self._token = token
                    return token
                last_error_code = data.get("errorCode", "MISSING_TOKEN")
                last_error_message = data.get(
                    "errorMessage", "200 response missing access_token"
                )
                continue

            last_error_code = data.get("errorCode", response.status_code)
            last_error_message = data.get(
                "errorMessage", response.text[:500] or response.reason
            )

        raise TokenError(last_error_code, last_error_message)

    def refresh_single_flight(self, stale_token: Optional[str] = None) -> str:
        """Single-flight refresh: first worker refreshes; others wait and reuse (§6.3)."""
        with self._refresh_lock:
            with self._lock:
                current = self._token
            # Another worker already refreshed past the stale token
            if (
                stale_token is not None
                and current is not None
                and current != stale_token
            ):
                return current
            return self.acquire()
