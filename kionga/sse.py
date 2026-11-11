"""Server-sent event streams that survive the gateway closing them.

The gateway ends a team run stream after five minutes and drops idle
connections, so the reader reconnects with ``Last-Event-ID`` and the stream
carries on from the last event it delivered. Each event is delivered once.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import httpx

from .errors import KiongaConnectionError, KiongaError, error_for_response

__all__ = ["ServerSentEvent", "StreamError", "parse_events"]

# The gateway sends a keep-alive comment every 15 seconds.
STREAM_TIMEOUT = httpx.Timeout(10.0, read=60.0)


@dataclass(frozen=True)
class ServerSentEvent:
    event: str
    data: str
    id: str = ""


class StreamError(KiongaError):
    """The gateway reported an error inside the stream, such as a failed log query."""


def parse_events(lines: Iterator[str]) -> Iterator[ServerSentEvent]:
    """Parse the text/event-stream format. Comments (keep-alives) are skipped."""
    event, data, event_id = "message", [], ""
    for line in lines:
        if not line:
            if data:
                yield ServerSentEvent(event, "\n".join(data), event_id)
            event, data = "message", []
            continue
        if line.startswith(":"):
            continue
        name, _, value = line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if name == "event":
            event = value
        elif name == "data":
            data.append(value)
        elif name == "id" and "\0" not in value:
            event_id = value
    if data:
        yield ServerSentEvent(event, "\n".join(data), event_id)


class StreamingAPI:
    """Needs ``_client``, ``retry`` and ``_sleep`` from the client."""

    _client: httpx.Client
    retry: Any
    _sleep: Any

    def _stream(self, path: str, *, params: dict[str, Any] | None = None,
                last_id: str = "") -> Iterator[ServerSentEvent]:
        """Yield events, reconnecting from the last id whenever the stream ends.

        Ends only when the caller stops iterating. Failures to connect use the
        client's retry policy; the count resets once a connection delivers events.
        """
        failures = 0
        while True:
            headers = {"Accept": "text/event-stream"}
            if last_id:
                headers["Last-Event-ID"] = last_id
            delivered = False
            try:
                with self._client.stream("GET", path, params=params, headers=headers,
                                         timeout=STREAM_TIMEOUT) as response:
                    if response.status_code >= 400:
                        response.read()
                        error = error_for_response(response)
                        if not (self.retry.should_retry_status(response.request, response)
                                and failures + 1 < self.retry.max_attempts):
                            raise error
                        failures += 1
                        self._sleep(self.retry.delay(failures, response))
                        continue
                    for event in parse_events(response.iter_lines()):
                        delivered = True
                        if event.id:
                            last_id = event.id
                        yield event
            except httpx.TransportError as error:
                if failures + 1 >= self.retry.max_attempts:
                    raise KiongaConnectionError(f"GET {path} stream failed: {error}") from error
                failures += 1
                self._sleep(self.retry.delay(failures))
                continue
            if delivered:
                failures = 0
            else:
                # Closed without sending anything: back off before reconnecting.
                failures = min(failures + 1, self.retry.max_attempts)
                self._sleep(self.retry.delay(failures))
