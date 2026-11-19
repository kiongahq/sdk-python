"""Applications client: create, read, change, operate and wait on applications."""
import json

import httpx
import pytest

import kionga
from kionga import (
    Application,
    ApplicationFailed,
    ApplicationsNotConfiguredError,
    ApplicationTimeout,
    ConflictError,
    NotConfiguredError,
    ValidationError,
)

BASE = "/api/v1/applications"
APP = {
    "id": "app-1", "name": "billing", "project_id": "p-1", "image": "ghcr.io/acme/billing:1.4",
    "port": 8080, "env": {"MODE": "prod"}, "secret_refs": ["billing-db"],
    "resources": {"cpu": "500m", "memory": "512Mi"}, "replicas": {"min": 0, "max": 4},
    "scaling": {"metric": "requests", "target": 20}, "health_path": "/healthz", "visibility": "sso",
    "status": {"phase": "pending", "url": "", "ready_replicas": 0, "conditions": None},
    "created_at": "2026-11-19T00:00:00Z",
}
NOT_CONFIGURED = {"error": "not_configured",
                  "message": "Applications need Kubernetes. Set applications.baseDomain in the chart."}


def with_status(**status):
    return {**APP, "status": {**APP["status"], **status}}


class Gateway:
    """Answers from a route table; a list answers each call in turn, repeating the last."""

    def __init__(self, routes):
        self.routes, self.seen, self.calls = routes, [], {}

    def __call__(self, request):
        self.seen.append(request)
        key = (request.method, request.url.raw_path.decode().split("?")[0])
        answer = self.routes[key]
        if isinstance(answer, list):
            index = self.calls.get(key, 0)
            self.calls[key] = index + 1
            answer = answer[min(index, len(answer) - 1)]
        return answer(request) if callable(answer) else answer

    def body(self, index=-1):
        return json.loads(self.seen[index].content)


def ok(body, status=200, **kwargs):
    return httpx.Response(status, json=body, **kwargs)


def client(routes):
    gateway = Gateway(routes)
    c = kionga.KiongaClient("http://gateway", transport=httpx.MockTransport(gateway))
    c.slept = []
    c._sleep = c.slept.append
    return c, gateway


def test_create_sends_the_full_spec():
    c, gw = client({("POST", BASE): ok(APP, 201)})
    app = c.create_application(
        "p-1", "billing", image="ghcr.io/acme/billing:1.4", env={"MODE": "prod"},
        secret_refs=["billing-db"], cpu="500m", memory="512Mi", min_replicas=0, max_replicas=4,
        scaling={"metric": "requests", "target": 20}, health_path="/healthz")
    assert gw.body() == {
        "project_id": "p-1", "name": "billing", "image": "ghcr.io/acme/billing:1.4", "port": 8080,
        "env": {"MODE": "prod"}, "secret_refs": ["billing-db"],
        "resources": {"cpu": "500m", "memory": "512Mi"}, "replicas": {"min": 0, "max": 4},
        "scaling": {"metric": "requests", "target": 20}, "health_path": "/healthz", "visibility": "sso",
    }
    assert (app.id, app.phase, app.scaling.metric, app.status.conditions) == ("app-1", "pending", "requests", [])
    assert not app.ready and not app.failed


def test_create_from_git_sends_only_the_source_fields_given_and_defaults_max_to_min():
    c, gw = client({("POST", BASE): ok(APP, 201)})
    c.create_application("p-1", "docs", source=kionga.GitSource(git="https://github.com/acme/docs", ref="v2"),
                         min_replicas=2, visibility="public")
    sent = gw.body()
    assert sent["source"] == {"git": "https://github.com/acme/docs", "ref": "v2"}
    assert "image" not in sent
    assert sent["replicas"] == {"min": 2, "max": 2}
    assert sent["visibility"] == "public"


def test_scale_to_zero_without_a_max_still_allows_one_replica():
    c, gw = client({("POST", BASE): ok(APP, 201)})
    c.create_application("p-1", "billing", image="i", min_replicas=0)
    assert gw.body()["replicas"] == {"min": 0, "max": 1}


@pytest.mark.parametrize("fields, message", [
    ({"name": "Billing"}, "DNS label"),
    ({"name": "1st"}, "DNS label"),
    ({"name": "a" * 64}, "DNS label"),
    ({"image": None}, "exactly one of image or source"),
    ({"source": {"git": "https://x"}}, "exactly one of image or source"),
    ({"port": 0}, "port"),
    ({"port": 70000}, "port"),
    ({"min_replicas": -1}, "negative"),
    ({"max_replicas": 0}, "at least 1"),
    ({"min_replicas": 3, "max_replicas": 2}, "at least min_replicas"),
    ({"scaling": {"metric": "memory", "target": 5}}, "metric"),
    ({"scaling": {"metric": "cpu", "target": 0}}, "target"),
    ({"health_path": "healthz"}, "start with /"),
    ({"visibility": "private"}, "visibility"),
    ({"env": {"BAD-NAME": "x"}}, "BAD-NAME"),
    ({"project_id": ""}, "project_id"),
])
def test_invalid_fields_are_rejected_before_anything_is_sent(fields, message):
    c, gw = client({})
    args = {"project_id": "p-1", "name": "billing", "image": "i", **fields}
    with pytest.raises(ValueError, match=message):
        c.create_application(args.pop("project_id"), args.pop("name"), **args)
    assert gw.seen == []


def test_gateway_validation_errors_carry_its_code_and_message():
    c, _ = client({("POST", BASE): ok({"error": "validation_error", "message": "image is not allowed"}, 422)})
    with pytest.raises(ValidationError) as caught:
        c.create_application("p-1", "billing", image="evil/image")
    assert (caught.value.code, caught.value.message) == ("validation_error", "image is not allowed")


def test_a_duplicate_name_is_a_conflict_not_a_configuration_error():
    c, _ = client({("POST", BASE): ok({"error": "already_exists", "message": "billing exists"}, 409)})
    with pytest.raises(ConflictError) as caught:
        c.create_application("p-1", "billing", image="i")
    assert not isinstance(caught.value, NotConfiguredError)


@pytest.mark.parametrize("call", [
    lambda c: c.list_applications(),
    lambda c: c.get_application("billing"),
    lambda c: c.create_application("p-1", "billing", image="i"),
    lambda c: c.update_application("billing", port=9000),
    lambda c: c.delete_application("billing"),
    lambda c: c.redeploy_application("billing"),
    lambda c: c.application_events("billing"),
    lambda c: c.application_logs("billing"),
])
def test_compose_installs_raise_a_clear_not_configured_error(call):
    answer = ok(NOT_CONFIGURED, 409)
    c, gw = client({(m, p): answer for m in ("GET", "POST", "PATCH", "DELETE")
                    for p in (BASE, f"{BASE}/billing", f"{BASE}/billing/redeploy",
                              f"{BASE}/billing/events", f"{BASE}/billing/logs")})
    with pytest.raises(ApplicationsNotConfiguredError) as caught:
        call(c)
    error = caught.value
    assert "Kubernetes install" in str(error)
    assert "applications.baseDomain" in str(error), "the gateway's enablement steps are kept"
    assert isinstance(error, NotConfiguredError) and error.status_code == 409
    assert len(gw.seen) == 1, "a configuration answer is not retried"


def test_not_configured_on_a_503_is_not_retried_either():
    c, gw = client({("GET", BASE): ok(NOT_CONFIGURED, 503)})
    with pytest.raises(ApplicationsNotConfiguredError):
        c.list_applications()
    assert len(gw.seen) == 1


def test_list_get_and_ids_are_escaped():
    c, gw = client({
        ("GET", BASE): ok({"items": [APP]}),
        ("GET", f"{BASE}/a%2Fb"): ok(APP),
    })
    [listed] = c.list_applications("p-1")
    assert gw.seen[0].url.params["project_id"] == "p-1"
    assert listed.secret_refs == ["billing-db"]
    c.get_application("a/b")
    assert gw.seen[-1].url.raw_path == b"/api/v1/applications/a%2Fb"


def test_update_sends_only_the_changed_fields():
    c, gw = client({("PATCH", f"{BASE}/billing"): ok(APP)})
    c.update_application("billing", image="ghcr.io/acme/billing:1.5", max_replicas=8)
    assert gw.body() == {"image": "ghcr.io/acme/billing:1.5", "replicas": {"max": 8}}
    with pytest.raises(ValueError, match="nothing to update"):
        c.update_application("billing")
    with pytest.raises(ValueError, match="not both"):
        c.update_application("billing", image="i", source={"git": "g"})


def test_delete_and_redeploy():
    c, gw = client({
        ("DELETE", f"{BASE}/billing"): httpx.Response(204),
        ("POST", f"{BASE}/billing/redeploy"): ok(with_status(phase="deploying"), 202),
    })
    assert c.delete_application("billing") is None
    assert c.redeploy_application("billing").phase == "deploying"
    assert gw.body() == {}


def test_events_accept_the_usual_kubernetes_timestamp_names():
    c, _ = client({("GET", f"{BASE}/billing/events"): ok({"items": [
        {"type": "Warning", "reason": "BackOff", "message": "restarting", "last_seen": "2026-11-19T01:00:00Z",
         "count": 3},
    ]})})
    [event] = c.application_events("billing")
    assert (event.type, event.reason, event.count, event.timestamp.hour) == ("Warning", "BackOff", 3, 1)


def test_logs_read_json_lines_or_plain_text():
    c, gw = client({("GET", f"{BASE}/billing/logs"): [
        ok({"items": [{"pod": "billing-0", "line": "started", "ts": "2026-11-19T00:00:00Z"}]}),
        httpx.Response(200, text="one\ntwo\n", headers={"content-type": "text/plain"}),
        httpx.Response(204),
    ]})
    [line] = c.application_logs("billing", tail=50)
    assert gw.seen[-1].url.params["tail"] == "50"
    assert (line.pod, line.message, line.timestamp.year) == ("billing-0", "started", 2026)
    assert [entry.message for entry in c.application_logs("billing")] == ["one", "two"]
    assert c.application_logs("billing") == []
    with pytest.raises(ValueError):
        c.application_logs("billing", tail=-1)


def test_logs_can_be_followed_over_server_sent_events():
    stream = (
        'event: log\nid: 1\ndata: {"pod": "billing-0", "message": "a"}\n\n'
        'event: log\nid: 2\ndata: [{"message": "b"}, {"message": "c"}]\n\n'
        'event: log\ndata: plain text\n\n'
        'event: end\ndata: {}\n\n'
    )
    c, gw = client({("GET", f"{BASE}/billing/logs/stream"): httpx.Response(
        200, text=stream, headers={"content-type": "text/event-stream"})})
    lines = list(c.stream_application_logs("billing", tail=10))
    assert [line.message for line in lines] == ["a", "b", "c", "plain text"]
    assert gw.seen[0].url.params["follow"] == "true"


def test_following_logs_on_compose_raises_the_clear_error():
    c, _ = client({("GET", f"{BASE}/billing/logs/stream"): ok(NOT_CONFIGURED, 409)})
    with pytest.raises(ApplicationsNotConfiguredError):
        list(c.stream_application_logs("billing"))


def test_wait_until_ready_polls_until_ready():
    c, _ = client({("GET", f"{BASE}/app-1"): [
        ok(with_status(phase="building")),
        ok(with_status(phase="deploying")),
        ok(with_status(phase="ready", url="https://billing.example.net", ready_replicas=1)),
    ]})
    app = c.wait_until_ready("app-1", poll_interval=2)
    assert app.ready and app.url == "https://billing.example.net"
    assert c.slept == [2, 2]


def test_a_ready_condition_counts_and_scale_to_zero_is_ready_with_no_replicas():
    ready = Application.model_validate(with_status(phase="scaled", conditions=[{"type": "Ready", "status": "True"}]))
    assert ready.ready and ready.status.ready_replicas == 0


def test_wait_raises_with_the_failure_reason_from_the_status():
    c, _ = client({("GET", f"{BASE}/app-1"): ok(with_status(
        phase="failed", conditions=[{"type": "Ready", "status": "False", "reason": "ImagePullBackOff",
                                     "message": "manifest unknown"}]))})
    with pytest.raises(ApplicationFailed) as caught:
        c.wait_until_ready("app-1")
    assert caught.value.reason == "ImagePullBackOff: manifest unknown"
    assert "manifest unknown" in str(caught.value)


def test_a_failed_build_is_the_reason_when_nothing_else_is():
    c, _ = client({("GET", f"{BASE}/app-1"): ok(with_status(
        phase="failed", last_build={"status": "failed", "message": "Dockerfile not found"}))})
    with pytest.raises(ApplicationFailed, match="build failed: Dockerfile not found"):
        c.wait_until_ready("app-1")


def test_without_a_reason_in_the_status_the_latest_warning_event_explains_the_failure():
    c, _ = client({
        ("GET", f"{BASE}/app-1"): ok(with_status(phase="failed")),
        ("GET", f"{BASE}/app-1/events"): ok({"items": [
            {"type": "Warning", "reason": "FailedScheduling", "message": "0/3 nodes have enough memory"},
            {"type": "Normal", "reason": "Scheduled", "message": "ok"},
        ]}),
    })
    with pytest.raises(ApplicationFailed) as caught:
        c.wait_until_ready("app-1")
    assert caught.value.reason == "FailedScheduling: 0/3 nodes have enough memory"


def test_wait_times_out_with_the_last_state_and_reason():
    c, gw = client({("GET", f"{BASE}/app-1"): ok(with_status(
        phase="deploying", message="Back-off pulling image ghcr.io/acme/billing:1.4"))})
    with pytest.raises(ApplicationTimeout) as caught:
        c.wait_until_ready("app-1", timeout=0, poll_interval=1)
    error = caught.value
    assert isinstance(error, TimeoutError)
    assert error.application.phase == "deploying"
    assert error.reason == "Back-off pulling image ghcr.io/acme/billing:1.4"
    assert "still deploying after 0s" in str(error)
    assert c.slept == [] and len(gw.seen) == 1


def test_a_reason_lookup_that_fails_does_not_hide_the_failure():
    c, _ = client({
        ("GET", f"{BASE}/app-1"): ok(with_status(phase="failed")),
        ("GET", f"{BASE}/app-1/events"): ok({"error": "forbidden"}, 403),
    })
    with pytest.raises(ApplicationFailed) as caught:
        c.wait_until_ready("app-1")
    assert caught.value.reason == ""


@pytest.mark.parametrize("body", [
    {"id": "a", "name": "n", "status": "ready", "url": "https://n.example"},
    {"id": "a", "name": "n", "phase": "ready", "url": "https://n.example", "ready_replicas": 2},
    {"id": "a", "name": "n", "status": {"phase": "ready", "url": "https://n.example"}},
])
def test_status_shapes_the_gateway_might_send_all_parse(body):
    app = Application.model_validate(body)
    assert app.ready and app.url == "https://n.example"


def test_null_collections_from_the_go_encoder_become_empty():
    app = Application.model_validate({"id": "a", "name": "n", "env": None, "secret_refs": None,
                                      "replicas": None, "resources": None})
    assert (app.env, app.secret_refs, app.replicas, app.resources) == ({}, [], {}, {})


@pytest.mark.asyncio
async def test_the_async_client_has_the_same_methods():
    gateway = Gateway({
        ("GET", BASE): ok({"items": [APP]}),
        ("GET", f"{BASE}/app-1"): [ok(with_status(phase="building")), ok(with_status(phase="running"))],
        ("POST", BASE): ok(NOT_CONFIGURED, 409),
    })
    slept = []

    async def sleep(seconds):
        slept.append(seconds)

    async with kionga.AsyncKiongaClient("http://gateway", transport=httpx.MockTransport(gateway)) as c:
        c._sleep = sleep
        [listed] = await c.list_applications()
        assert listed.name == "billing"
        assert (await c.wait_until_ready("app-1")).phase == "running"
        with pytest.raises(ApplicationsNotConfiguredError):
            await c.create_application("p-1", "billing", image="i")
    assert len(slept) == 1


def test_a_not_configured_409_is_still_a_conflict_for_existing_handlers():
    c, _ = client({("GET", BASE): ok(NOT_CONFIGURED, 409)})
    with pytest.raises(ConflictError) as caught:
        c.list_applications()
    assert isinstance(caught.value, ApplicationsNotConfiguredError)


def test_a_not_configured_503_is_still_a_server_error():
    c, _ = client({("GET", BASE): ok(NOT_CONFIGURED, 503)})
    with pytest.raises(kionga.ServerError) as caught:
        c.list_applications()
    assert isinstance(caught.value, ApplicationsNotConfiguredError)


def test_a_status_from_the_previous_rollout_is_not_ready():
    stale = Application.model_validate({**with_status(phase="ready", observed_generation=3), "generation": 4})
    assert not stale.ready
    current = Application.model_validate({**with_status(phase="ready", observed_generation=4), "generation": 4})
    assert current.ready


def test_wait_after_an_update_skips_the_previous_rollouts_ready_status():
    c, _ = client({("GET", f"{BASE}/app-1"): [
        ok(with_status(phase="ready", observed_generation=3)),
        ok(with_status(phase="ready", observed_generation=4)),
    ]})
    app = c.wait_until_ready("app-1", generation=4)
    assert app.status.observed_generation == 4 and len(c.slept) == 1


def stream(text):
    return httpx.Response(200, text=text, headers={"content-type": "text/event-stream"})


def test_a_resumed_log_stream_does_not_repeat_lines():
    first = (
        'event: log\ndata: {"pod": "p", "message": "a", "ts": "2026-11-19T00:00:01Z"}\n\n'
        'event: log\ndata: {"pod": "p", "message": "b", "ts": "2026-11-19T00:00:02Z"}\n\n'
    )
    # The gateway replays from `since`, inclusive, after the drop.
    second = (
        'event: log\ndata: {"pod": "p", "message": "b", "ts": "2026-11-19T00:00:02Z"}\n\n'
        'event: log\ndata: {"pod": "p", "message": "c", "ts": "2026-11-19T00:00:02Z"}\n\n'
        'event: log\ndata: {"pod": "p", "message": "d", "ts": "2026-11-19T00:00:03Z"}\n\n'
        'event: end\ndata: {}\n\n'
    )
    c, gw = client({("GET", f"{BASE}/billing/logs/stream"): [stream(first), stream(second)]})
    assert [line.message for line in c.stream_application_logs("billing", tail=20)] == ["a", "b", "c", "d"]
    assert gw.seen[0].url.params["tail"] == "20"
    resumed = gw.seen[1].url.params
    assert resumed["since"].startswith("2026-11-19T00:00:02") and "tail" not in resumed


def test_a_resumed_log_stream_without_timestamps_asks_for_new_lines_only():
    c, gw = client({("GET", f"{BASE}/billing/logs/stream"): [
        stream("event: log\ndata: repeated\n\n"),
        stream("event: log\ndata: new\n\nevent: end\ndata: {}\n\n"),
    ]})
    assert [line.message for line in c.stream_application_logs("billing", tail=5)] == ["repeated", "new"]
    assert gw.seen[1].url.params["tail"] == "0"
