"""Applications client against the gateway's /api/v1/applications contract.

Fixtures follow the gateway's JSON: an application is addressed by name, its phase
is ``status`` with ``url``, ``ready_replicas``, ``message`` and ``conditions``
beside it, logs come per pod, and Compose answers 409 ``not_configured`` with
``steps``.
"""
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
    ServerError,
    ValidationError,
)

BASE = "/api/v1/applications"
APP = {
    "name": "billing", "project_id": "p-1", "image": "ghcr.io/acme/billing:1.4", "port": 8080,
    "env": {"MODE": "prod"}, "secret_refs": ["billing-db"], "resources": {"cpu": "500m", "memory": "512Mi"},
    "replicas": {"min": 0, "max": 4}, "scaling": {"metric": "requests", "target": 20},
    "health_path": "/healthz", "visibility": "sso", "owner_subject": "me", "url": "https://billing.example.net",
    "status": "pending", "ready_replicas": 0, "created_at": "2026-11-19T00:00:00Z",
    "updated_at": "2026-11-19T00:00:00Z",
}
NOT_CONFIGURED = {
    "error": "not_configured", "message": "Applications run only on Kubernetes. This install runs on Compose.",
    "steps": ["Install Kionga on Kubernetes with the Helm chart; Compose installs do not run applications.",
              "Set applications.baseDomain in the chart values."],
}


def app(**fields):
    return {**APP, **fields}


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


def ok(body, status=200):
    return httpx.Response(status, json=body)


def client(routes):
    gateway = Gateway(routes)
    c = kionga.KiongaClient("http://gateway", transport=httpx.MockTransport(gateway))
    c.slept = []
    c._sleep = c.slept.append
    return c, gateway


def test_create_sends_the_full_spec_and_parses_the_gateway_response():
    c, gw = client({("POST", BASE): ok(APP, 201)})
    created = c.create_application(
        "p-1", "billing", image="ghcr.io/acme/billing:1.4", port=8080, env={"MODE": "prod"},
        secret_refs=["billing-db"], cpu="500m", memory="512Mi", min_replicas=0, max_replicas=4,
        scaling={"metric": "requests", "target": 20}, health_path="/healthz", visibility="sso")
    assert gw.body() == {
        "project_id": "p-1", "name": "billing", "image": "ghcr.io/acme/billing:1.4", "port": 8080,
        "env": {"MODE": "prod"}, "secret_refs": ["billing-db"],
        "resources": {"cpu": "500m", "memory": "512Mi"}, "replicas": {"min": 0, "max": 4},
        "scaling": {"metric": "requests", "target": 20}, "health_path": "/healthz", "visibility": "sso",
    }
    assert (created.id, created.name, created.phase, created.url) == (
        "billing", "billing", "pending", "https://billing.example.net")
    assert created.scaling.metric == "requests" and created.owner_subject == "me"
    assert not created.ready and not created.failed


def test_fields_left_out_take_the_gateway_defaults():
    c, gw = client({("POST", BASE): ok(APP, 201)})
    c.create_application("p-1", "docs", source=kionga.GitSource(git="https://github.com/acme/docs", ref="v2"))
    assert gw.body() == {"project_id": "p-1", "name": "docs",
                         "source": {"git": "https://github.com/acme/docs", "ref": "v2"}}


@pytest.mark.parametrize("given, sent", [
    ({"min_replicas": 0}, {"min": 0, "max": 1}),
    ({"min_replicas": 2}, {"min": 2, "max": 2}),
    ({"max_replicas": 5}, {"min": 1, "max": 5}),
])
def test_one_replica_bound_fills_the_other_because_the_gateway_needs_both(given, sent):
    c, gw = client({("POST", BASE): ok(APP, 201)})
    c.create_application("p-1", "billing", image="i", **given)
    assert gw.body()["replicas"] == sent


@pytest.mark.parametrize("fields, message", [
    ({"name": "Billing"}, "DNS label"),
    ({"name": "1st"}, "DNS label"),
    ({"name": "a" * 64}, "DNS label"),
    ({"image": None}, "exactly one of image or source"),
    ({"source": {"git": "https://x"}}, "exactly one of image or source"),
    ({"port": 0}, "port"),
    ({"port": 70000}, "port"),
    ({"min_replicas": -1}, "negative"),
    ({"max_replicas": 0}, "1 to 100"),
    ({"max_replicas": 101}, "1 to 100"),
    ({"min_replicas": 3, "max_replicas": 2}, "at least min_replicas"),
    ({"scaling": {"metric": "memory", "target": 5}}, "metric"),
    ({"scaling": {"metric": "cpu", "target": -1}}, "1 to 100"),
    ({"scaling": {"metric": "cpu", "target": 101}}, "1 to 100"),
    ({"scaling": {"metric": "requests", "target": 10001}}, "1 to 10000"),
    ({"scaling": {"metric": "cpu", "target": 70}, "min_replicas": 0}, "scale to zero needs the requests"),
    ({"health_path": "healthz"}, "start with /"),
    ({"visibility": "private"}, "visibility"),
    ({"env": {"BAD-NAME": "x"}}, "BAD-NAME"),
    ({"env": {"KIONGA_TOKEN": "x"}}, "reserved"),
    ({"project_id": ""}, "project_id"),
])
def test_invalid_fields_are_rejected_before_anything_is_sent(fields, message):
    c, gw = client({})
    args = {"project_id": "p-1", "name": "billing", "image": "i", **fields}
    with pytest.raises(ValueError, match=message):
        c.create_application(args.pop("project_id"), args.pop("name"), **args)
    assert gw.seen == []


def test_gateway_validation_errors_carry_its_code_and_message():
    body = {"error": "validation_error", "message": "name: \"admin\" is reserved by the platform",
            "details": [{"field": "name", "message": "\"admin\" is reserved by the platform"}]}
    c, _ = client({("POST", BASE): ok(body, 422)})
    with pytest.raises(ValidationError) as caught:
        c.create_application("p-1", "admin", image="i")
    assert caught.value.code == "validation_error" and "reserved" in caught.value.message
    assert caught.value.response.json()["details"][0]["field"] == "name"


def test_a_duplicate_name_is_a_conflict_not_a_configuration_error():
    c, _ = client({("POST", BASE): ok({"error": "conflict", "message": "an application named billing already exists"}, 409)})
    with pytest.raises(ConflictError) as caught:
        c.create_application("p-1", "billing", image="i")
    assert not isinstance(caught.value, NotConfiguredError)


ALL_ROUTES = [(m, p) for m in ("GET", "POST", "PUT", "DELETE")
              for p in (BASE, f"{BASE}/billing", f"{BASE}/billing/redeploy", f"{BASE}/billing/events",
                        f"{BASE}/billing/logs")]


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
    c, gw = client({route: ok(NOT_CONFIGURED, 409) for route in ALL_ROUTES})
    with pytest.raises(ApplicationsNotConfiguredError) as caught:
        call(c)
    error = caught.value
    assert str(error).startswith("Applications run only on a Kubernetes install of Kionga.")
    assert "This install runs on Compose." in str(error)
    assert "(2) Set applications.baseDomain in the chart values." in str(error)
    assert error.steps == NOT_CONFIGURED["steps"]
    assert isinstance(error, ConflictError) and error.status_code == 409, "existing 409 handlers still catch it"
    assert len(gw.seen) == 1, "a configuration answer is not retried"


def test_not_configured_on_a_503_is_a_server_error_and_not_retried():
    c, gw = client({("GET", BASE): ok(NOT_CONFIGURED, 503)})
    with pytest.raises(ServerError) as caught:
        c.list_applications()
    assert isinstance(caught.value, ApplicationsNotConfiguredError)
    assert len(gw.seen) == 1


def test_list_filters_by_project_and_get_escapes_the_name():
    c, gw = client({
        ("GET", BASE): ok({"configured": True, "base_domain": "example.net", "items": [APP], "total": 1}),
        ("GET", f"{BASE}/a%2Fb"): ok(APP),
    })
    [listed] = c.list_applications("p-1")
    assert gw.seen[0].url.params["project_id"] == "p-1"
    assert listed.secret_refs == ["billing-db"]
    c.get_application("a/b")
    assert gw.seen[-1].url.raw_path == b"/api/v1/applications/a%2Fb"


def test_update_reads_the_configuration_and_puts_it_back_with_the_changes():
    c, gw = client({("GET", f"{BASE}/billing"): ok(APP), ("PUT", f"{BASE}/billing"): ok(app(status="pending"), 202)})
    c.update_application("billing", image="ghcr.io/acme/billing:1.5", max_replicas=8)
    assert [r.method for r in gw.seen] == ["GET", "PUT"]
    assert gw.body() == {
        "project_id": "p-1", "name": "billing", "image": "ghcr.io/acme/billing:1.5", "port": 8080,
        "env": {"MODE": "prod"}, "secret_refs": ["billing-db"], "resources": {"cpu": "500m", "memory": "512Mi"},
        "replicas": {"min": 0, "max": 8}, "scaling": {"metric": "requests", "target": 20},
        "health_path": "/healthz", "visibility": "sso",
    }


def test_update_switching_to_a_git_source_drops_the_image():
    c, gw = client({("GET", f"{BASE}/billing"): ok(APP), ("PUT", f"{BASE}/billing"): ok(APP, 202)})
    c.update_application("billing", source={"git": "https://github.com/acme/billing"})
    sent = gw.body()
    assert "image" not in sent and sent["source"] == {"git": "https://github.com/acme/billing"}


def test_update_validates_before_writing():
    c, gw = client({("GET", f"{BASE}/billing"): ok(APP)})
    with pytest.raises(ValueError, match="nothing to update"):
        c.update_application("billing")
    with pytest.raises(ValueError, match="not both"):
        c.update_application("billing", image="i", source={"git": "g"})
    with pytest.raises(ValueError, match="scale to zero"):
        c.update_application("billing", scaling={"metric": "cpu", "target": 50})  # current min is 0
    assert all(r.method == "GET" for r in gw.seen)


# run_as_user: the numeric user the containers run as. PUT replaces the whole
# configuration, so an update must send back the value the application has.

def test_create_sends_run_as_user_only_when_given():
    c, gw = client({("POST", BASE): ok(app(run_as_user=1000), 201)})
    created = c.create_application("p-1", "billing", image="i", run_as_user=1000)
    assert gw.body()["run_as_user"] == 1000 and created.run_as_user == 1000
    c.create_application("p-1", "billing", image="i")
    assert "run_as_user" not in gw.body()
    assert Application.model_validate(APP).run_as_user is None
    assert Application.model_validate(app(run_as_user=1000)).request()["run_as_user"] == 1000


def test_update_keeps_the_run_as_user_the_application_has():
    # The regression: the body was rebuilt without run_as_user, the PUT cleared it,
    # and an image that needs it could no longer start.
    c, gw = client({("GET", f"{BASE}/billing"): ok(app(run_as_user=1000)),
                    ("PUT", f"{BASE}/billing"): ok(app(run_as_user=1000), 202)})
    c.update_application("billing", image="ghcr.io/acme/billing:1.5")
    assert gw.body()["run_as_user"] == 1000 and gw.body()["image"] == "ghcr.io/acme/billing:1.5"
    c.update_application("billing", max_replicas=8, visibility="public")
    assert gw.body()["run_as_user"] == 1000


def test_update_changes_or_clears_run_as_user_only_when_asked():
    routes = {("GET", f"{BASE}/billing"): ok(app(run_as_user=1000)), ("PUT", f"{BASE}/billing"): ok(APP, 202)}
    c, gw = client(routes)
    c.update_application("billing", run_as_user=2000)  # a change on its own is an update
    assert gw.body()["run_as_user"] == 2000 and gw.body()["image"] == APP["image"]
    c.update_application("billing", run_as_user=kionga.CLEAR)
    assert "run_as_user" not in gw.body() and gw.body()["image"] == APP["image"]
    c.update_application("billing", run_as_user=kionga.CLEAR, port=9000)
    assert "run_as_user" not in gw.body() and gw.body()["port"] == 9000
    # Clearing what was never set still writes, and sends none.
    c, gw = client({("GET", f"{BASE}/billing"): ok(APP), ("PUT", f"{BASE}/billing"): ok(APP, 202)})
    c.update_application("billing", run_as_user=kionga.CLEAR)
    assert gw.seen[-1].method == "PUT" and "run_as_user" not in gw.body()
    c.update_application("billing", run_as_user=1000)
    assert gw.body()["run_as_user"] == 1000


def test_clear_is_one_value_and_only_an_update_takes_it():
    from kionga.applications import CLEAR, _Clear
    assert kionga.CLEAR is CLEAR is _Clear() and repr(CLEAR) == "kionga.CLEAR"
    c, gw = client({("POST", BASE): ok(APP, 201)})
    with pytest.raises(ValueError, match="run_as_user"):
        c.create_application("p-1", "billing", image="i", run_as_user=kionga.CLEAR)
    assert gw.seen == []


@pytest.mark.parametrize("value, message", [
    (0, "never run as root"), (-1, "1 to 2147483647"), (2147483648, "1 to 2147483647"),
    (True, "whole number"), ("1000", "whole number"), (1000.0, "whole number"),
])
def test_a_run_as_user_the_gateway_would_refuse_is_rejected_before_writing(value, message):
    c, gw = client({("GET", f"{BASE}/billing"): ok(APP)})
    with pytest.raises(ValueError, match=message):
        c.create_application("p-1", "billing", image="i", run_as_user=value)
    with pytest.raises(ValueError, match=message):
        c.update_application("billing", run_as_user=value)
    assert all(r.method == "GET" for r in gw.seen)
    c, gw = client({("POST", BASE): ok(app(run_as_user=2147483647), 201)})
    assert c.create_application("p-1", "billing", image="i", run_as_user=2147483647).run_as_user == 2147483647
    assert c.create_application("p-1", "billing", image="i", run_as_user=1) and gw.body()["run_as_user"] == 1


def test_update_of_an_internal_application_is_not_refused_by_the_client():
    # The gateway accepts visibility "internal" (no host). The client's own check
    # used to know only public and sso, so any update of such an application failed.
    c, gw = client({("GET", f"{BASE}/billing"): ok(app(visibility="internal")),
                    ("PUT", f"{BASE}/billing"): ok(app(visibility="internal"), 202)})
    c.update_application("billing", max_replicas=8)
    assert gw.body()["visibility"] == "internal" and gw.body()["replicas"] == {"min": 0, "max": 8}
    c, gw = client({("POST", BASE): ok(app(visibility="internal"), 201)})
    c.create_application("p-1", "billing", image="i", visibility="internal")
    assert gw.body()["visibility"] == "internal"


def test_an_update_sends_back_every_request_field_the_gateway_returned():
    # Every field of the gateway's ApplicationRequest, as its Application returns them.
    full = app(image="", source={"git": "https://g", "ref": "v2", "context_dir": "svc", "dockerfile": "D"},
               run_as_user=1000, visibility="internal")
    c, gw = client({("GET", f"{BASE}/billing"): ok(full), ("PUT", f"{BASE}/billing"): ok(full, 202)})
    c.update_application("billing", port=9000)
    assert gw.body() == {
        "project_id": "p-1", "name": "billing",
        "source": {"git": "https://g", "ref": "v2", "context_dir": "svc", "dockerfile": "D"}, "port": 9000,
        "env": {"MODE": "prod"}, "secret_refs": ["billing-db"], "resources": {"cpu": "500m", "memory": "512Mi"},
        "replicas": {"min": 0, "max": 4}, "scaling": {"metric": "requests", "target": 20},
        "health_path": "/healthz", "visibility": "internal", "run_as_user": 1000,
    }


@pytest.mark.asyncio
async def test_the_async_client_keeps_and_clears_run_as_user():
    gateway = Gateway({("GET", f"{BASE}/billing"): ok(app(run_as_user=1000)),
                       ("PUT", f"{BASE}/billing"): ok(app(run_as_user=1000), 202),
                       ("POST", BASE): ok(app(run_as_user=1000), 201)})
    async with kionga.AsyncKiongaClient("http://gateway", transport=httpx.MockTransport(gateway)) as c:
        await c.update_application("billing", image="ghcr.io/acme/billing:1.5")
        assert gateway.body()["run_as_user"] == 1000
        await c.update_application("billing", run_as_user=kionga.CLEAR)
        assert "run_as_user" not in gateway.body()
        assert (await c.create_application("p-1", "billing", image="i", run_as_user=1000)).run_as_user == 1000
        with pytest.raises(ValueError, match="never run as root"):
            await c.update_application("billing", run_as_user=0)


def test_delete_and_redeploy():
    c, gw = client({
        ("DELETE", f"{BASE}/billing"): httpx.Response(204),
        ("POST", f"{BASE}/billing/redeploy"): ok(app(status="pending"), 202),
    })
    assert c.delete_application("billing") is None
    assert c.redeploy_application("billing").phase == "pending"
    assert gw.body() == {}


def test_events_parse_the_gateway_shape():
    c, _ = client({("GET", f"{BASE}/billing/events"): ok({"items": [
        {"type": "Warning", "reason": "BackOff", "message": "restarting", "object": "Pod/billing-0",
         "count": 3, "last_seen": "2026-11-19T01:00:00Z", "first_seen": "2026-11-19T00:00:00Z"},
    ], "total": 1})})
    [event] = c.application_events("billing")
    assert (event.type, event.reason, event.object, event.count, event.timestamp.hour) == (
        "Warning", "BackOff", "Pod/billing-0", 3, 1)


def test_logs_come_per_pod_with_tail_and_container():
    c, gw = client({("GET", f"{BASE}/billing/logs"): ok({"items": [
        {"pod": "billing-0", "container": "app", "phase": "Running", "lines": "started\nlistening on 8080\n"},
        {"pod": "billing-1", "container": "app", "phase": "Pending", "lines": "", "error": "container creating"},
    ], "total": 2, "tail": 50})})
    first, second = c.application_logs("billing", tail=50, container="app")
    assert gw.seen[-1].url.params["tail"] == "50" and gw.seen[-1].url.params["container"] == "app"
    assert first.messages == ["started", "listening on 8080"]
    assert (second.messages, second.error) == ([], "container creating")
    c.application_logs("billing")
    assert "tail" not in gw.seen[-1].url.params, "the gateway's default applies"


@pytest.mark.parametrize("tail", [0, 2001])
def test_a_tail_outside_the_gateway_limits_is_rejected(tail):
    c, gw = client({})
    with pytest.raises(ValueError, match="1 to 2000"):
        c.application_logs("billing", tail=tail)
    assert gw.seen == []


def test_wait_until_ready_polls_until_ready():
    c, _ = client({("GET", f"{BASE}/billing"): [
        ok(app(status="pending", message="building image")),
        ok(app(status="pending")),
        ok(app(status="ready", ready_replicas=1)),
    ]})
    ready = c.wait_until_ready("billing", poll_interval=2)
    assert ready.ready and ready.url == "https://billing.example.net"
    assert c.slept == [2, 2]


def test_scale_to_zero_is_ready_with_no_replicas():
    idle = Application.model_validate(app(status="scaled-to-zero", ready_replicas=0))
    assert idle.ready and not idle.failed


def test_wait_raises_with_the_failure_reason_from_the_status():
    c, _ = client({("GET", f"{BASE}/billing"): ok(app(
        status="failed", message="ImagePullBackOff: manifest unknown",
        conditions=[{"type": "Ready", "status": "False", "reason": "ImagePullBackOff"}]))})
    with pytest.raises(ApplicationFailed) as caught:
        c.wait_until_ready("billing")
    assert caught.value.reason == "ImagePullBackOff: manifest unknown"
    assert "manifest unknown" in str(caught.value)


def test_a_missing_resource_fails_instead_of_waiting():
    c, _ = client({("GET", f"{BASE}/billing"): ok(app(
        status="missing", message="the KiongaApplication resource does not exist"))})
    with pytest.raises(ApplicationFailed, match="does not exist"):
        c.wait_until_ready("billing")


def test_a_failing_condition_explains_a_failure_without_a_message():
    c, _ = client({("GET", f"{BASE}/billing"): ok(app(status="failed", conditions=[
        {"type": "Ready", "status": "False", "reason": "CrashLoopBackOff", "message": "exit code 1"}]))})
    with pytest.raises(ApplicationFailed) as caught:
        c.wait_until_ready("billing")
    assert caught.value.reason == "CrashLoopBackOff: exit code 1"


def test_a_failed_build_is_the_reason_when_nothing_else_is():
    c, _ = client({("GET", f"{BASE}/billing"): ok(app(
        status="failed", last_build={"status": "failed", "message": "Dockerfile not found"}))})
    with pytest.raises(ApplicationFailed, match="build failed: Dockerfile not found"):
        c.wait_until_ready("billing")


def test_without_a_reason_in_the_status_the_newest_warning_event_explains_the_failure():
    c, _ = client({
        ("GET", f"{BASE}/billing"): ok(app(status="failed")),
        ("GET", f"{BASE}/billing/events"): ok({"items": [  # the gateway sends newest first
            {"type": "Warning", "reason": "FailedScheduling", "message": "0/3 nodes have enough memory",
             "last_seen": "2026-11-19T02:00:00Z"},
            {"type": "Normal", "reason": "Scheduled", "message": "ok", "last_seen": "2026-11-19T01:30:00Z"},
            {"type": "Warning", "reason": "BackOff", "message": "old", "last_seen": "2026-11-19T01:00:00Z"},
        ]}),
    })
    with pytest.raises(ApplicationFailed) as caught:
        c.wait_until_ready("billing")
    assert caught.value.reason == "FailedScheduling: 0/3 nodes have enough memory"


def test_wait_times_out_with_the_last_state_and_reason():
    c, gw = client({("GET", f"{BASE}/billing"): ok(app(
        status="pending", message="Back-off pulling image ghcr.io/acme/billing:1.4"))})
    with pytest.raises(ApplicationTimeout) as caught:
        c.wait_until_ready("billing", timeout=0, poll_interval=1)
    error = caught.value
    assert isinstance(error, TimeoutError)
    assert error.application.phase == "pending"
    assert error.reason == "Back-off pulling image ghcr.io/acme/billing:1.4"
    assert "still pending after 0s" in str(error)
    assert c.slept == [] and len(gw.seen) == 1


def test_a_reason_lookup_that_fails_does_not_hide_the_failure():
    c, _ = client({
        ("GET", f"{BASE}/billing"): ok(app(status="failed")),
        ("GET", f"{BASE}/billing/events"): ok({"error": "kubernetes_unavailable"}, 502),
    })
    with pytest.raises(ApplicationFailed) as caught:
        c.wait_until_ready("billing")
    assert caught.value.reason == ""


def test_a_status_from_the_previous_rollout_is_not_ready_when_generations_are_reported():
    stale = Application.model_validate(app(status="ready", observed_generation=3, generation=4))
    assert not stale.ready
    assert Application.model_validate(app(status="ready", observed_generation=4, generation=4)).ready


def test_wait_with_a_generation_skips_the_previous_rollout():
    c, _ = client({("GET", f"{BASE}/billing"): [
        ok(app(status="ready", observed_generation=3)),
        ok(app(status="ready", observed_generation=4)),
    ]})
    assert c.wait_until_ready("billing", generation=4).status.observed_generation == 4
    assert len(c.slept) == 1


def test_wait_with_a_generation_falls_back_to_the_phase_when_reads_carry_none():
    c, _ = client({("GET", f"{BASE}/billing"): ok(app(status="ready"))})
    assert c.wait_until_ready("billing", generation=5).ready


def test_a_nested_status_object_also_parses():
    nested = Application.model_validate({"name": "n", "status": {"phase": "ready", "url": "https://n.example"}})
    assert nested.ready and nested.url == "https://n.example" and nested.id == "n"


def test_null_collections_from_the_go_encoder_become_empty():
    parsed = Application.model_validate({"name": "n", "env": None, "secret_refs": None, "replicas": None,
                                         "resources": None, "conditions": None})
    assert (parsed.env, parsed.secret_refs, parsed.replicas, parsed.resources, parsed.status.conditions) == (
        {}, [], {}, {}, [])


def test_request_round_trips_only_the_fields_a_write_accepts():
    body = Application.model_validate(app(source={"git": "https://g", "ref": "main", "dockerfile": "Dockerfile"},
                                          image="")).request()
    assert set(body) <= {"name", "project_id", "image", "source", "port", "env", "secret_refs", "resources",
                         "replicas", "scaling", "health_path", "visibility", "run_as_user"}
    assert "image" not in body and body["source"]["git"] == "https://g"


@pytest.mark.asyncio
async def test_the_async_client_has_the_same_methods():
    gateway = Gateway({
        ("GET", BASE): ok({"items": [APP]}),
        ("GET", f"{BASE}/billing"): [ok(app(status="pending")), ok(app(status="ready"))],
        ("POST", BASE): ok(NOT_CONFIGURED, 409),
    })
    slept = []

    async def sleep(seconds):
        slept.append(seconds)

    async with kionga.AsyncKiongaClient("http://gateway", transport=httpx.MockTransport(gateway)) as c:
        c._sleep = sleep
        [listed] = await c.list_applications()
        assert listed.name == "billing"
        assert (await c.wait_until_ready("billing")).phase == "ready"
        with pytest.raises(ApplicationsNotConfiguredError):
            await c.create_application("p-1", "billing", image="i")
    assert len(slept) == 1


def test_a_scaling_rule_without_a_target_takes_the_gateway_default():
    c, gw = client({("POST", BASE): ok(APP, 201)})
    c.create_application("p-1", "billing", image="i", scaling={"metric": "requests"})
    assert gw.body()["scaling"] == {"metric": "requests"}
