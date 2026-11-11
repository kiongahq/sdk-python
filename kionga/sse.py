"""Server-sent event streams that survive the gateway closing them.

The gateway ends a team run stream after five minutes and drops idle
connections, so the reader reconnects with ``Last-Event-ID`` and the stream
carries on from the last event it delivered. Each event is delivered once.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from .errors import KiongaError

__all__ = ["ServerSentEvent", "StreamError"]

# The gateway sends a keep-alive comment every 15 seconds.
STREAM_TIMEOUT = httpx.Timeout(10.0, read=60.0)


@dataclass(frozen=True)
class ServerSentEvent:
    event: str
    data: str
    id: str = ""


class StreamError(KiongaError):
    """The gateway reported an error inside the stream, such as a failed log query."""
