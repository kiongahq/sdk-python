"""AsyncKiongaClient: the same behaviour as the sync client, awaited."""
import json

import httpx
import pytest

from kionga import (
    AsyncKiongaClient, KiongaConnectionError, NotFoundError, RetryPolicy, ServerError, TeamRun,
)

pytestmark = pytest.mark.asyncio


def scripted(*answers):
    seen = []

    def handler(request):
        seen.append(request)
        answer = answers[min(len(seen), len(answers)) - 1]
        if isinstance(answer, Exception):
            raise answer
        return answer(request) if callable(answer) else answer

    return handler, seen


def client(handler, **kwargs):
    c = AsyncKiongaClient("http://gateway", transport=httpx.MockTransport(handler), **kwargs)
    c.slept = []

    async def sleep(seconds):
        c.slept.append(seconds)

    c._sleep = sleep
    return c


def ok(body, status=200):
    return httpx.Response(status, json=body)


async def test_requests_are_awaited_and_retried_with_an_awaited_backoff():
    handler, seen = scripted(ok({"error": "busy"}, 503), ok({"status": "ok"}))
    async with client(handler) as c:
        assert await c.health() == {"status": "ok"}
    assert len(seen) == 2 and len(c.slept) == 1


async def test_errors_are_the_same_typed_errors():
    handler, seen = scripted(ok({"error": "not_found", "message": "no run"}, 404))
    async with client(handler) as c:
        with pytest.raises(NotFoundError) as caught:
            await c.get_team_run("atr-x")
    assert caught.value.code == "not_found" and len(seen) == 1


async def test_an_unkeyed_post_is_not_repeated():
    handler, seen = scripted(ok({}, 503), ok({}))
    async with client(handler) as c:
        with pytest.raises(ServerError):
            await c._request("POST", "/api/v1/projects", json={})
    assert len(seen) == 1


async def test_a_connection_that_never_opens_raises_after_the_policy():
    handler, seen = scripted(httpx.ConnectError("refused"))
    async with client(handler, retry=RetryPolicy(max_attempts=2)) as c:
        with pytest.raises(KiongaConnectionError):
            await c.health()
    assert len(seen) == 2


async def test_models_are_the_same_classes_as_the_sync_client_returns():
    handler, _ = scripted(ok({"id": "atr-1", "status": "succeeded"}))
    async with client(handler) as c:
        run = await c.get_team_run("atr-1")
    assert type(run) is TeamRun and run.finished


async def test_a_retried_team_run_start_keeps_one_idempotency_key():
    handler, seen = scripted(ok({}, 503), ok({"id": "atr-1", "status": "running"}, 201))
    async with client(handler) as c:
        await c.start_team_run("atm-1", "go")
    keys = {json.loads(r.content)["idempotency_key"] for r in seen} | {r.headers["Idempotency-Key"] for r in seen}
    assert len(seen) == 2 and len(keys) == 1


async def test_waiting_polls_until_the_run_finishes():
    states = iter(["running", "succeeded"])
    handler, seen = scripted(lambda r: ok({"id": "atr-1", "status": next(states)}))
    async with client(handler) as c:
        assert (await c.wait_for_team_run("atr-1")).status == "succeeded"
    assert len(seen) == 2


async def test_async_iterators_page_and_walk():
    pages = iter([[{"sequence": 1, "ts": "2026-11-10T00:00:00Z", "source": "runner", "severity": "info", "message": "a"}], []])
    listings = {"": {"objects": [{"key": "x"}], "prefixes": ["d/"]}, "d/": {"objects": [{"key": "d/y"}], "prefixes": []}}

    def route(request):
        if request.url.path == "/api/v1/logs":
            return ok({"items": next(pages)})
        return ok(listings[request.url.params["prefix"]])

    handler, _ = scripted(route)
    async with client(handler) as c:
        assert [e.sequence async for e in c.iter_logs(page_size=1)] == [1]
        assert [o["key"] async for o in c.walk_storage("b")] == ["x", "d/y"]


async def test_a_team_stream_reconnects_from_the_last_event_and_stops_at_end():
    def stream(*blocks):
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, content="".join(blocks).encode())

    event = lambda s: f"id: {s}\nevent: run\ndata: {json.dumps({'seq': s, 'type': 't'})}\n\n"  # noqa: E731
    handler, seen = scripted(stream(event(1)), stream(event(2), "event: end\ndata: {}\n\n"))
    async with client(handler) as c:
        assert [e.seq async for e in c.stream_team_run_events("atr-1")] == [1, 2]
    assert seen[1].headers["Last-Event-ID"] == "1"


async def test_closing_the_client_closes_its_connections():
    handler, _ = scripted(ok({}))
    c = client(handler)
    await c.aclose()
    assert c._client.is_closed
