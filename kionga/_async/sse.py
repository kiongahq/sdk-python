# Async source of truth. kionga/_sync is generated from this by scripts/unasync.py.
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx

from ..errors import KiongaConnectionError, error_for_response
from ..sse import STREAM_TIMEOUT, ServerSentEvent

__all__ = ["StreamingAPI", "parse_events"]


async def parse_events(lines: AsyncIterator[str]) -> AsyncIterator[ServerSentEvent]:
    """Parse the text/event-stream format. Comments (keep-alives) are skipped."""
    event, data, event_id = "message", [], ""
    async for line in lines:
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

    _client: httpx.AsyncClient
    retry: Any
    _sleep: Any

    async def _stream(self, path: str, *, params: dict[str, Any] | None = None,
                last_id: str = "") -> AsyncIterator[ServerSentEvent]:
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
                async with self._client.stream("GET", path, params=params, headers=headers,
                                         timeout=STREAM_TIMEOUT) as response:
                    if response.status_code >= 400:
                        await response.aread()
                        error = error_for_response(response)
                        if not (self.retry.should_retry_status(response.request, response)
                                and failures + 1 < self.retry.max_attempts):
                            raise error
                        failures += 1
                        await self._sleep(self.retry.delay(failures, response))
                        continue
                    async for event in parse_events(response.aiter_lines()):
                        delivered = True
                        if event.id:
                            last_id = event.id
                        yield event
            except httpx.TransportError as error:
                if failures + 1 >= self.retry.max_attempts:
                    raise KiongaConnectionError(f"GET {path} stream failed: {error}") from error
                failures += 1
                await self._sleep(self.retry.delay(failures))
                continue
            if delivered:
                failures = 0
            else:
                # Closed without sending anything: back off before reconnecting.
                failures = min(failures + 1, self.retry.max_attempts)
                await self._sleep(self.retry.delay(failures))
