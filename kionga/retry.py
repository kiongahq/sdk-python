"""When and how long the client waits before trying a request again.

A request is retried only when repeating it can't do something twice:

* the connection failed before the request was sent;
* the method is idempotent (GET, HEAD, OPTIONS, PUT, DELETE);
* a POST or PATCH carries an ``Idempotency-Key``, so the gateway deduplicates it;
* the gateway answered 429, which it does before doing any work.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

import httpx

from .errors import retry_after_seconds

__all__ = ["RetryPolicy"]

IDEMPOTENT = frozenset({"GET", "HEAD", "OPTIONS", "PUT", "DELETE"})


@dataclass(frozen=True)
class RetryPolicy:
    """Retry settings. ``RetryPolicy(max_attempts=1)`` turns retries off."""

    max_attempts: int = 3
    backoff: float = 0.5
    max_backoff: float = 8.0
    statuses: frozenset[int] = field(default_factory=lambda: frozenset({429, 502, 503, 504}))

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.backoff < 0 or self.max_backoff < 0:
            raise ValueError("backoff values can't be negative")

    def safe_to_repeat(self, request: httpx.Request) -> bool:
        return request.method in IDEMPOTENT or bool(request.headers.get("Idempotency-Key"))

    def should_retry_status(self, request: httpx.Request, response: httpx.Response) -> bool:
        if response.status_code not in self.statuses:
            return False
        return response.status_code == 429 or self.safe_to_repeat(request)

    def should_retry_error(self, request: httpx.Request, error: httpx.TransportError) -> bool:
        # A failed connect means nothing reached the gateway.
        return isinstance(error, httpx.ConnectError) or self.safe_to_repeat(request)

    def delay(self, attempt: int, response: httpx.Response | None = None) -> float:
        """Seconds to wait after failed attempt number ``attempt`` (1-based)."""
        if response is not None:
            hinted = retry_after_seconds(response)
            if hinted is not None:
                return min(hinted, self.max_backoff)
        ceiling = min(self.max_backoff, self.backoff * 2 ** (attempt - 1))
        return random.uniform(ceiling / 2, ceiling)  # jitter spreads clients apart
