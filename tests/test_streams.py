"""Server-sent event streams: parsing, reconnecting, and ending."""
import json

import httpx
import pytest

import kionga
from kionga import KiongaConnectionError, NotFoundError, RetryPolicy, StreamError
from kionga.sse import parse_events


def sse(*blocks):
    return httpx.Response(200, headers={"Content-Type": "text/event-stream"},
                          content="".join(blocks).encode())


def run_event(seq, kind="worker.started"):
    return f"id: {seq}\nevent: run\ndata: {json.dumps({'seq': seq, 'type': kind})}\n\n"


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


def test_the_parser_joins_data_lines_keeps_ids_and_skips_keep_alives():
    lines = iter([": keep-alive", "", "id: 7", "event: run", "data: {\"a\":", "data: 1}", "",
                  "data: no-event-name", ""])
    events = list(parse_events(lines))
    assert [(e.event, e.data, e.id) for e in events] == [("run", '{"a":\n1}', "7"), ("message", "no-event-name", "7")]


def test_a_team_stream_ends_at_the_end_event():
    c, seen = client([sse(": keep-alive\n\n", run_event(1), run_event(2, "run.finished"),
                          'event: end\ndata: {"status":"succeeded"}\n\n')])
    events = list(c.stream_team_run_events("atr-1", after=0))
    assert [(e.seq, e.type) for e in events] == [(1, "worker.started"), (2, "run.finished")]
    assert seen[0].url.path == "/api/v1/team-runs/atr-1/events/stream"
    assert seen[0].headers["Accept"] == "text/event-stream" and "Last-Event-ID" not in seen[0].headers


def test_a_stream_the_gateway_closes_is_reopened_from_the_last_event_without_repeats():
    c, seen = client([sse(run_event(1), run_event(2)),
                      sse(run_event(3), 'event: end\ndata: {"status":"failed"}\n\n')])
    assert [e.seq for e in c.stream_team_run_events("atr-1")] == [1, 2, 3]
    assert seen[1].headers["Last-Event-ID"] == "2"
    assert c.slept == [], "a stream that delivered events reconnects at once"


def test_a_dropped_connection_is_retried_with_backoff_then_resumes():
    c, seen = client([sse(run_event(1)), httpx.ReadError("reset"),
                      sse(run_event(2), 'event: end\ndata: {}\n\n')])
    assert [e.seq for e in c.stream_team_run_events("atr-1")] == [1, 2]
    assert seen[2].headers["Last-Event-ID"] == "1" and len(c.slept) == 1


def test_a_stream_that_never_connects_gives_up_after_the_policy():
    c, seen = client([httpx.ConnectError("refused")], retry=RetryPolicy(max_attempts=3))
    with pytest.raises(KiongaConnectionError):
        list(c.stream_team_run_events("atr-1"))
    assert len(seen) == 3


def test_a_missing_run_raises_without_reconnecting():
    c, seen = client([httpx.Response(404, json={"error": "not_found", "message": "run not found"})])
    with pytest.raises(NotFoundError):
        list(c.stream_team_run_events("atr-x"))
    assert len(seen) == 1


def test_a_log_stream_yields_each_entry_of_each_batch_and_sends_the_filters():
    batch = [{"sequence": s, "ts": "2026-11-10T00:00:00Z", "source": "runner", "severity": "error", "message": "m"}
             for s in (4, 5)]
    c, seen = client([sse(f"id: 5\nevent: logs\ndata: {json.dumps(batch)}\n\n")])
    stream = c.stream_logs(run_id="run-1", severities=["error"], after=3)
    assert [next(stream).sequence, next(stream).sequence] == [4, 5]
    assert dict(seen[0].url.params) == {"run_id": "run-1", "severity": "error", "after": "3"}


def test_a_failed_log_query_inside_the_stream_raises():
    c, _ = client([sse('event: error\ndata: "log query failed"\n\n')])
    with pytest.raises(StreamError, match="log query failed"):
        next(c.stream_logs())


def test_a_log_stream_rejects_a_bad_filter_before_connecting():
    c, seen = client([sse()])
    with pytest.raises(ValueError):
        next(c.stream_logs(severities=["fatal"]))
    assert seen == []
