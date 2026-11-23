"""Followed application logs: the gateway's Server-Sent Events stream.

Fixtures follow the gateway: ``id: <RFC 3339 nano>``, ``event: log`` with
``{pod, container, time, line}``, and ``event: end`` with a reason. The gateway
closes a stream after ``max_seconds`` ("max_seconds reached; ...") and skips lines
at or before the resume point; a stricter or looser server must not change what
the caller sees.
"""
import http.server
import json
import threading
from datetime import datetime, timedelta, timezone

import httpx
import pytest

import kionga
from kionga import (
    ApplicationLogLine,
    ApplicationsNotConfiguredError,
    AsyncKiongaClient,
    ConflictError,
    KiongaConnectionError,
    NotFoundError,
    RetryPolicy,
    ServerError,
    StreamError,
)
from kionga.applications import LogCursor, instant, rewind

PATH = "/api/v1/applications/billing/logs"
SLICE = 'event: end\ndata: {"reason":"max_seconds reached; reconnect with Last-Event-ID to continue"}\n\n'
DONE = 'event: end\ndata: {"reason":"all pod log streams closed"}\n\n'
T = "2026-11-24T10:00:00"


def log(time, line, pod="billing-0", container="app"):
    body = json.dumps({"pod": pod, "container": container, "time": time, "line": line})
    head = f"id: {time}\n" if time else ""
    return f"{head}event: log\ndata: {body}\n\n"


def sse(*blocks):
    return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, content="".join(blocks).encode())


def client(answers, **kwargs):
    seen = []

    def handler(request):
        seen.append(request)
        answer = answers[min(len(seen), len(answers)) - 1]
        if isinstance(answer, Exception):
            raise answer
        return answer

    c = kionga.KiongaClient("http://gateway", transport=httpx.MockTransport(handler), **kwargs)
    c.slept = []
    c._sleep = c.slept.append
    return c, seen


def lines(c, **kwargs):
    return [(entry.pod, entry.line) for entry in c.follow_application_logs("billing", **kwargs)]


def test_a_followed_stream_yields_each_line_and_ends_with_the_reason():
    c, seen = client([sse(": keep-alive\n\n", log(f"{T}.1Z", "starting"), log(f"{T}.2Z", "listening"), DONE)])
    reasons = []
    got = list(c.follow_application_logs("billing", tail=50, container="app", on_end=reasons.append))
    assert [(e.pod, e.container, e.line, e.time) for e in got] == [
        ("billing-0", "app", "starting", f"{T}.1Z"), ("billing-0", "app", "listening", f"{T}.2Z")]
    assert reasons == ["all pod log streams closed"]
    assert seen[0].url.path == PATH and len(seen) == 1
    assert dict(seen[0].url.params) == {"follow": "true", "tail": "50", "container": "app"}
    assert seen[0].headers["Accept"] == "text/event-stream" and "Last-Event-ID" not in seen[0].headers
    assert got[0].timestamp.microsecond == 100000 and got[0].timestamp.tzinfo is not None


def test_since_and_max_seconds_are_sent_and_a_datetime_is_normalised():
    c, seen = client([sse(DONE)])
    lines(c, since="2026-11-24T09:59:00.5Z", max_seconds=30)
    assert dict(seen[0].url.params) == {"follow": "true", "since": "2026-11-24T09:59:00.5Z", "max_seconds": "30"}
    c, seen = client([sse(DONE)])
    lines(c, since=datetime(2026, 11, 24, 12, 0, tzinfo=timezone(timedelta(hours=2))))
    assert seen[0].url.params["since"] == "2026-11-24T10:00:00Z"


@pytest.mark.parametrize("kwargs, message", [
    ({"tail": 0}, "1 to 2000"), ({"tail": 2001}, "1 to 2000"),
    ({"max_seconds": 0}, "1 to 3600"), ({"max_seconds": 3601}, "1 to 3600"),
    ({"since": "yesterday"}, "RFC 3339"), ({"since": "2026-11-24 10:00:00"}, "RFC 3339"),
    ({"since": datetime(2026, 11, 24)}, "time zone"),
])
def test_bad_arguments_fail_before_anything_is_sent(kwargs, message):
    c, seen = client([sse(DONE)])
    with pytest.raises(ValueError, match=message):
        lines(c, **kwargs)
    assert seen == []


def test_a_stream_closed_at_its_time_limit_resumes_from_the_last_line_without_repeats():
    # The real gateway resumes after the id; the second answer repeats nothing.
    c, seen = client([sse(log(f"{T}.100Z", "a"), log(f"{T}.200Z", "b"), SLICE),
                      sse(log(f"{T}.300Z", "c"), DONE)])
    assert lines(c) == [("billing-0", "a"), ("billing-0", "b"), ("billing-0", "c")]
    assert len(seen) == 2 and c.slept == []
    assert seen[1].headers["Last-Event-ID"] == f"{T}.199999999Z"


def test_a_server_that_repeats_the_last_instant_does_not_cause_duplicates():
    # Resuming one nanosecond early asks the server to resend the last instant, so a
    # line cut off at that instant is recovered. The ones already delivered are skipped.
    c, _ = client([sse(log(f"{T}.5Z", "a"), log(f"{T}.5Z", "b"), SLICE),
                   sse(log(f"{T}.5Z", "a"), log(f"{T}.5Z", "b"), log(f"{T}.5Z", "c"), log(f"{T}.6Z", "d"), DONE)])
    assert lines(c) == [("billing-0", "a"), ("billing-0", "b"), ("billing-0", "c"), ("billing-0", "d")]


def test_identical_lines_are_never_dropped_or_repeated():
    same = "GET /healthz 200"
    first = sse(log(f"{T}.1Z", same), log(f"{T}.2Z", same), log(f"{T}.2Z", same),
                log(f"{T}.2Z", same, pod="billing-1"), SLICE)
    # The second connection replays the last instant (3 lines), then 1 genuinely new identical one.
    second = sse(log(f"{T}.2Z", same), log(f"{T}.2Z", same), log(f"{T}.2Z", same, pod="billing-1"),
                 log(f"{T}.2Z", same), log(f"{T}.3Z", same), DONE)
    c, _ = client([first, second])
    assert lines(c) == [("billing-0", same)] * 3 + [("billing-1", same)] + [("billing-0", same)] * 2


def test_a_line_that_arrives_out_of_order_within_a_connection_is_still_delivered():
    c, _ = client([sse(log(f"{T}.5Z", "from-a", pod="a"), log(f"{T}.4Z", "from-b", pod="b"), DONE)])
    assert lines(c) == [("a", "from-a"), ("b", "from-b")]


def test_lines_without_a_time_are_delivered_and_do_not_move_the_resume_point():
    c, seen = client([sse(log("", "no stamp"), log(f"{T}Z", "stamped"), log("", "no stamp"), SLICE),
                      sse(DONE)])
    assert lines(c) == [("billing-0", "no stamp"), ("billing-0", "stamped"), ("billing-0", "no stamp")]
    assert seen[1].headers["Last-Event-ID"] == "2026-11-24T09:59:59.999999999Z"


def test_a_dropped_connection_is_retried_with_backoff_and_resumes_after_the_last_line():
    c, seen = client([sse(log(f"{T}.1Z", "a")), httpx.ReadError("reset"),
                      sse(log(f"{T}.2Z", "b"), DONE)])
    assert lines(c) == [("billing-0", "a"), ("billing-0", "b")]
    assert len(seen) == 3 and len(c.slept) == 1
    assert seen[2].headers["Last-Event-ID"] == f"{T}.099999999Z"


def test_a_stream_that_never_connects_gives_up_after_the_policy():
    c, seen = client([httpx.ConnectError("refused")], retry=RetryPolicy(max_attempts=3))
    with pytest.raises(KiongaConnectionError):
        lines(c)
    assert len(seen) == 3


def test_an_unavailable_gateway_is_retried_then_followed():
    c, seen = client([httpx.Response(502, json={"error": "kubernetes_unavailable", "message": "listing pods failed"}),
                      sse(log(f"{T}.1Z", "a"), DONE)])
    assert lines(c) == [("billing-0", "a")]
    assert len(seen) == 2 and len(c.slept) == 1


def test_a_bad_status_raises_its_error_without_reconnecting():
    c, seen = client([httpx.Response(404, json={"error": "not_found", "message": "application not found"})])
    with pytest.raises(NotFoundError):
        lines(c)
    c, seen = client([httpx.Response(400, json={"error": "invalid_request", "message": "max_seconds must be between 1 and 3600"})])
    with pytest.raises(kionga.KiongaAPIError, match="max_seconds"):
        lines(c)
    assert len(seen) == 1
    c, seen = client([httpx.Response(503, json={"error": "unavailable"})], retry=RetryPolicy(max_attempts=2))
    with pytest.raises(ServerError):
        lines(c)
    assert len(seen) == 2


@pytest.mark.parametrize("status, parent", [(409, ConflictError), (503, ServerError)])
def test_a_compose_install_raises_the_applications_not_configured_error_at_once(status, parent):
    body = {"error": "not_configured", "message": "Applications run only on Kubernetes.",
            "steps": ["Install Kionga on Kubernetes with the Helm chart."]}
    c, seen = client([httpx.Response(status, json=body)])
    with pytest.raises(ApplicationsNotConfiguredError) as caught:
        lines(c)
    assert isinstance(caught.value, parent) and "Helm chart" in str(caught.value)
    assert len(seen) == 1, "a configuration answer is not retried"


def test_a_malformed_log_event_raises_stream_error():
    c, _ = client([sse("event: log\ndata: not json\n\n")])
    with pytest.raises(StreamError, match="malformed"):
        lines(c)


def test_unknown_events_and_an_end_without_a_reason_are_tolerated():
    reasons = []
    c, _ = client([sse("event: notice\ndata: hello\n\n", log(f"{T}.1Z", "a"), "event: end\ndata: {}\n\n")])
    assert [e.line for e in c.follow_application_logs("billing", on_end=reasons.append)] == ["a"]
    assert reasons == [""]


def test_leaving_the_loop_early_closes_the_stream_and_asks_for_nothing_more():
    c, seen = client([sse(log(f"{T}.1Z", "a"), log(f"{T}.2Z", "b"), SLICE), sse(DONE)])
    stream = c.follow_application_logs("billing")
    assert next(stream).line == "a"
    stream.close()
    assert len(seen) == 1


def test_the_application_name_is_escaped_in_the_path():
    c, seen = client([sse(DONE)])
    list(c.follow_application_logs("a/b"))
    assert seen[0].url.raw_path.decode().startswith("/api/v1/applications/a%2Fb/logs")


# The resume point and the cursor are pure functions.

def test_rewind_steps_back_one_nanosecond_in_the_gateways_format():
    assert rewind("2026-11-24T10:00:00.123456789Z") == "2026-11-24T10:00:00.123456788Z"
    assert rewind("2026-11-24T10:00:00.5Z") == "2026-11-24T10:00:00.499999999Z"
    assert rewind("2026-11-24T10:00:00.000000001Z") == "2026-11-24T10:00:00Z"
    assert rewind("2026-11-24T10:00:00Z") == "2026-11-24T09:59:59.999999999Z"
    assert rewind("2026-11-24T12:00:00+02:00") == "2026-11-24T09:59:59.999999999Z"
    assert rewind("not a time") == "not a time"
    assert instant("2026-11-24T10:00:00.5Z") == instant("2026-11-24T12:00:00.500+02:00")
    assert instant("2026-13-24T10:00:00Z") is None


def test_the_cursor_counts_replays_per_connection():
    cursor = LogCursor()
    one = ApplicationLogLine(pod="p", container="c", time=f"{T}.1Z", line="x")
    cursor.connected()
    assert [cursor.accept(one), cursor.accept(one)] == [True, True]
    cursor.connected()
    assert [cursor.accept(one), cursor.accept(one), cursor.accept(one)] == [False, False, True]


# A real socket: chunked event stream, the connection closed by the server, resumed by header.

def test_against_a_local_server_the_stream_resumes_across_a_closed_connection():
    requests = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.path, self.headers.get("Last-Event-ID")))
            body = (log(f"{T}.1Z", "one") + log(f"{T}.2Z", "two") + SLICE if len(requests) == 1
                    else log(f"{T}.2Z", "two") + log(f"{T}.3Z", "three") + DONE)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()
            for block in body.split("\n\n")[:-1]:
                self.wfile.write((block + "\n\n").encode())
                self.wfile.flush()
            self.close_connection = True

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with kionga.KiongaClient(f"http://127.0.0.1:{server.server_port}") as c:
            assert lines(c, tail=10) == [("billing-0", "one"), ("billing-0", "two"), ("billing-0", "three")]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
    assert requests[0] == (f"{PATH}?follow=true&tail=10", None)
    assert requests[1][1] == f"{T}.199999999Z"


# The same behaviour, awaited.

@pytest.mark.asyncio
async def test_the_async_client_follows_resumes_and_ends():
    answers = [sse(log(f"{T}.1Z", "a"), log(f"{T}.1Z", "a"), SLICE),
               sse(log(f"{T}.1Z", "a"), log(f"{T}.1Z", "a"), log(f"{T}.2Z", "b"), DONE)]
    seen = []

    def handler(request):
        seen.append(request)
        return answers[len(seen) - 1]

    async with AsyncKiongaClient("http://gateway", transport=httpx.MockTransport(handler)) as c:
        reasons = []
        got = [e.line async for e in c.follow_application_logs("billing", on_end=reasons.append)]
    assert got == ["a", "a", "b"] and reasons == ["all pod log streams closed"]
    assert seen[1].headers["Last-Event-ID"] == f"{T}.099999999Z"


@pytest.mark.asyncio
async def test_the_async_client_raises_not_configured_and_closes_early():
    body = {"error": "not_configured", "message": "no", "steps": []}
    async with AsyncKiongaClient("http://gateway", transport=httpx.MockTransport(
            lambda request: httpx.Response(409, json=body))) as c:
        with pytest.raises(ApplicationsNotConfiguredError):
            async for _ in c.follow_application_logs("billing"):
                pass
    seen = []

    def handler(request):
        seen.append(request)
        return sse(log(f"{T}.1Z", "a"), SLICE)

    async with AsyncKiongaClient("http://gateway", transport=httpx.MockTransport(handler)) as c:
        stream = c.follow_application_logs("billing")
        assert (await stream.__anext__()).line == "a"
        await stream.aclose()
    assert len(seen) == 1
