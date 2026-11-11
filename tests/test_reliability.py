"""Retries and typed errors: when the client repeats a request, and what it raises."""
import httpx
import pytest

import kionga
from kionga import (
    ConflictError, KiongaAPIError, KiongaConnectionError, KiongaTimeoutError, NotFoundError,
    RateLimitError, RetryPolicy, ServerError, ValidationError,
)


def client(handler, **kwargs):
    c = kionga.KiongaClient("http://gateway", transport=httpx.MockTransport(handler), **kwargs)
    c.slept = []
    c._sleep = c.slept.append
    return c


def scripted(*responses):
    """Answer with each response in turn and record what was requested."""
    seen = []

    def handler(request):
        seen.append(request)
        item = responses[min(len(seen), len(responses)) - 1]
        if isinstance(item, Exception):
            raise item
        return item

    return handler, seen


def error(status, code="", message="", headers=None):
    return httpx.Response(status, json={"error": code, "message": message}, headers=headers or {})


OK = httpx.Response(200, json={"status": "ok"})


@pytest.mark.parametrize("status", [502, 503, 504])
def test_a_get_is_retried_on_a_gateway_failure(status):
    handler, seen = scripted(error(status), OK)
    with client(handler) as c:
        assert c.health() == {"status": "ok"}
    assert len(seen) == 2 and len(c.slept) == 1


def test_a_post_without_an_idempotency_key_is_not_repeated_after_a_server_failure():
    handler, seen = scripted(error(503), OK)
    with client(handler) as c, pytest.raises(ServerError):
        c._request("POST", "/api/v1/projects", json={"name": "x"})
    assert len(seen) == 1


def test_a_post_with_an_idempotency_key_is_retried():
    handler, seen = scripted(error(503), OK)
    with client(handler) as c:
        c._request("POST", "/api/v1/x", json={}, headers={"Idempotency-Key": "k1"})
    assert len(seen) == 2
    assert all(r.headers["Idempotency-Key"] == "k1" for r in seen)


def test_a_throttled_post_is_retried_after_the_wait_the_gateway_asked_for():
    handler, seen = scripted(error(429, "rate_limited", headers={"Retry-After": "2"}), OK)
    with client(handler) as c:
        c._request("POST", "/api/v1/x", json={})
    assert len(seen) == 2 and c.slept == [2.0]


def test_retry_after_is_capped_by_max_backoff():
    handler, _ = scripted(error(429, headers={"Retry-After": "3600"}), OK)
    with client(handler, retry=RetryPolicy(max_backoff=5)) as c:
        c.health()
    assert c.slept == [5]


def test_retries_stop_at_max_attempts_and_raise_the_last_error():
    handler, seen = scripted(error(429, "rate_limited", "slow down", {"Retry-After": "0"}))
    with client(handler, retry=RetryPolicy(max_attempts=4)) as c, pytest.raises(RateLimitError) as caught:
        c.health()
    assert len(seen) == 4 and caught.value.retry_after == 0
    assert caught.value.code == "rate_limited" and caught.value.message == "slow down"


def test_a_failed_connect_is_retried_even_for_a_post():
    handler, seen = scripted(httpx.ConnectError("refused"), OK)
    with client(handler) as c:
        c._request("POST", "/api/v1/x", json={})
    assert len(seen) == 2


def test_a_post_that_timed_out_is_not_repeated_because_it_may_have_run():
    handler, seen = scripted(httpx.ReadTimeout("slow"), OK)
    with client(handler) as c, pytest.raises(KiongaTimeoutError):
        c._request("POST", "/api/v1/x", json={})
    assert len(seen) == 1


def test_a_get_that_keeps_failing_to_connect_raises_a_connection_error():
    handler, seen = scripted(httpx.ConnectError("refused"))
    with client(handler) as c, pytest.raises(KiongaConnectionError) as caught:
        c.health()
    assert len(seen) == 3 and "3 attempt(s)" in str(caught.value)


def test_retries_can_be_turned_off():
    handler, seen = scripted(error(503), OK)
    with client(handler, retry=RetryPolicy(max_attempts=1)) as c, pytest.raises(ServerError):
        c.health()
    assert len(seen) == 1


@pytest.mark.parametrize("status,kind", [(404, NotFoundError), (409, ConflictError), (422, ValidationError), (500, ServerError)])
def test_each_status_raises_its_own_error_with_the_gateway_explanation(status, kind):
    handler, seen = scripted(httpx.Response(status, json={"error": "e_code", "message": "why"}, headers={"X-Request-ID": "req-1"}))
    with client(handler) as c, pytest.raises(kind) as caught:
        c._request("POST", "/api/v1/projects", json={})
    e = caught.value
    assert (e.status_code, e.code, e.message, e.request_id) == (status, "e_code", "why", "req-1")
    assert "POST /api/v1/projects returned" in str(e) and len(seen) == 1


def test_errors_are_still_httpx_status_errors_for_existing_callers():
    handler, _ = scripted(error(404, "not_found"))
    with client(handler) as c, pytest.raises(httpx.HTTPStatusError) as caught:
        c.health()
    assert isinstance(caught.value, KiongaAPIError) and caught.value.response.status_code == 404


def test_a_non_json_error_body_keeps_its_text():
    handler, _ = scripted(httpx.Response(502, text="upstream exploded"))
    with client(handler, retry=RetryPolicy(max_attempts=1)) as c, pytest.raises(ServerError) as caught:
        c.health()
    assert caught.value.code == "" and caught.value.message == "upstream exploded"


def test_backoff_grows_with_jitter_and_stays_under_the_cap():
    policy = RetryPolicy(backoff=1, max_backoff=4)
    for attempt, ceiling in [(1, 1), (2, 2), (3, 4), (8, 4)]:
        for _ in range(50):
            assert ceiling / 2 <= policy.delay(attempt) <= ceiling


@pytest.mark.parametrize("kwargs", [{"max_attempts": 0}, {"backoff": -1}])
def test_an_invalid_policy_is_rejected(kwargs):
    with pytest.raises(ValueError):
        RetryPolicy(**kwargs)


def test_an_empty_success_body_returns_none():
    handler, _ = scripted(httpx.Response(200))
    with client(handler) as c:
        assert c._request("DELETE", "/api/v1/x") is None
