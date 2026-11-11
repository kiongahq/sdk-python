"""Typed errors for gateway responses.

Every HTTP error the client raises is a ``KiongaAPIError``. It also subclasses
``httpx.HTTPStatusError``, so code that already catches that keeps working.
"""

from __future__ import annotations

import httpx

__all__ = [
    "AuthenticationError",
    "BadRequestError",
    "ConflictError",
    "KiongaAPIError",
    "KiongaConnectionError",
    "KiongaError",
    "KiongaTimeoutError",
    "NotFoundError",
    "PermissionDeniedError",
    "RateLimitError",
    "ServerError",
    "ValidationError",
    "error_for_response",
]


class KiongaError(Exception):
    """Base class for every error the SDK raises."""


class KiongaConnectionError(KiongaError):
    """The gateway could not be reached, after any retries."""


class KiongaTimeoutError(KiongaConnectionError):
    """The gateway did not answer within the timeout, after any retries."""


class KiongaAPIError(KiongaError, httpx.HTTPStatusError):
    """The gateway answered with an error status.

    ``code`` is the gateway's machine-readable error (for example
    ``validation_error``), ``message`` its explanation, and ``request_id`` the
    value of ``X-Request-ID`` when the gateway sent one.
    """

    def __init__(self, response: httpx.Response) -> None:
        self.status_code = response.status_code
        self.code, self.message = _error_body(response)
        self.request_id = response.headers.get("X-Request-ID", "")
        request = response.request
        text = f"{request.method} {request.url.path} returned {self.status_code}"
        if self.code:
            text += f" {self.code}"
        if self.message:
            text += f": {self.message}"
        httpx.HTTPStatusError.__init__(self, text, request=request, response=response)


class BadRequestError(KiongaAPIError):
    """400: the request was malformed."""


class AuthenticationError(KiongaAPIError):
    """401: no valid token or session."""


class PermissionDeniedError(KiongaAPIError):
    """403: the caller may not do this."""


class NotFoundError(KiongaAPIError):
    """404: no such resource, or the caller can't see it."""


class ConflictError(KiongaAPIError):
    """409: the resource changed or already exists."""


class ValidationError(KiongaAPIError):
    """422: the request was well formed but rejected."""


class RateLimitError(KiongaAPIError):
    """429: throttled. ``retry_after`` is the wait in seconds, when sent."""

    def __init__(self, response: httpx.Response) -> None:
        super().__init__(response)
        self.retry_after = retry_after_seconds(response)


class ServerError(KiongaAPIError):
    """5xx: the gateway or a service behind it failed."""


_BY_STATUS: dict[int, type[KiongaAPIError]] = {
    400: BadRequestError,
    401: AuthenticationError,
    403: PermissionDeniedError,
    404: NotFoundError,
    409: ConflictError,
    422: ValidationError,
    429: RateLimitError,
}


def error_for_response(response: httpx.Response) -> KiongaAPIError:
    """The most specific error class for an error response."""
    if response.status_code >= 500:
        return ServerError(response)
    return _BY_STATUS.get(response.status_code, KiongaAPIError)(response)


def retry_after_seconds(response: httpx.Response) -> float | None:
    """``Retry-After`` in seconds; the HTTP-date form is not used by the gateway."""
    value = response.headers.get("Retry-After", "").strip()
    try:
        seconds = float(value)
    except ValueError:
        return None
    return seconds if seconds >= 0 else None


def _error_body(response: httpx.Response) -> tuple[str, str]:
    try:
        body = response.json()
    except ValueError:
        return "", response.text[:500].strip()
    if not isinstance(body, dict):
        return "", ""
    code = body.get("error")
    message = body.get("message")
    return (code if isinstance(code, str) else ""), (message if isinstance(message, str) else "")
