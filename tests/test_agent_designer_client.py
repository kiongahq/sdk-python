"""Client contract for the native Agent Designer API: runs wait for a terminal
state, revisions and fixture revisions are sent for optimistic concurrency, and
idempotency keys and side-effect approval reach the gateway unchanged."""

import json

import httpx
import pytest

from mlaiops_sdk import MLAIOpsClient

DESIGN = {"id": "agd-1", "project_id": "prj-1", "name": "Support", "description": "", "revision": 3,
          "nodes": [{"id": "input", "kind": "input", "name": "In"}, {"id": "output", "kind": "output", "name": "Out"}],
          "edges": [{"from": "input", "to": "output"}]}


def client_for(route):
    return MLAIOpsClient("http://gateway", token="t", transport=httpx.MockTransport(route))


def test_design_lifecycle_requests():
    seen = []
    polls = {"run": 0, "evaluation": 0}

    def route(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        seen.append((request.method, request.url.path, body, request.headers.get("idempotency-key")))
        path = request.url.path
        if path == "/api/v1/agent-designs" and request.method == "POST":
            return httpx.Response(201, json={**DESIGN, "revision": 1})
        if path == "/api/v1/agent-designs/agd-1" and request.method == "PUT":
            return httpx.Response(200, json={**DESIGN, "revision": 4})
        if path == "/api/v1/agent-designs/agd-1/runs":
            return httpx.Response(202, json={"id": "agr-1", "status": "running"})
        if path == "/api/v1/agent-designs/agd-1/runs/agr-1":
            polls["run"] += 1
            return httpx.Response(200, json={"id": "agr-1", "status": "succeeded" if polls["run"] > 1 else "running", "output": "hi"})
        if path == "/api/v1/agent-designs/agd-1/evaluations":
            return httpx.Response(202, json={"id": "age-1", "status": "running"})
        if path == "/api/v1/agent-evaluations/age-1":
            polls["evaluation"] += 1
            return httpx.Response(200, json={"id": "age-1", "status": "completed", "pass_rate": 1.0, "passed": True})
        if path == "/api/v1/agent-designs/agd-1/publish":
            return httpx.Response(201, json={"id": "agv-1", "version": 1, "content_sha256": "a" * 64})
        if path == "/api/v1/agent-versions/agv-1/deploy":
            return httpx.Response(202, json={"id": "agt-1", "version": "v1"})
        return httpx.Response(404, json={"error": "not_found"})

    with client_for(route) as client:
        created = client.create_agent_design("prj-1", "Support", {"nodes": DESIGN["nodes"], "edges": DESIGN["edges"], "settings": {"max_steps": 10}})
        assert created["revision"] == 1
        client.save_agent_design(DESIGN)
        run = client.run_agent_design("agd-1", "hello", idempotency_key="k-1", poll_interval=0)
        assert run["status"] == "succeeded" and run["output"] == "hi"
        evaluation = client.evaluate_agent_design("agd-1", gate=0.9, poll_interval=0)
        assert evaluation["passed"] is True
        version = client.publish_agent_design("agd-1", 4, notes="first", evaluation_id="age-1")
        client.deploy_agent_version(version["id"], require_evaluation=True)

    create = seen[0]
    assert create[2]["settings"] == {"max_steps": 10} and create[2]["revision"] == 0
    assert seen[1][2]["revision"] == 3, "saves send the revision that was read"
    start = next(item for item in seen if item[1].endswith("/runs"))
    assert start[3] == "k-1" and start[2]["allow_side_effects"] is False
    assert next(item for item in seen if item[1].endswith("/evaluations"))[2]["gate"] == 0.9
    assert next(item for item in seen if item[1].endswith("/publish"))[2] == {"revision": 4, "notes": "first", "evaluation_id": "age-1"}
    assert next(item for item in seen if item[1].endswith("/deploy"))[2]["require_evaluation"] is True


def test_wait_times_out_and_conflicts_raise():
    def route(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/runs/agr-1"):
            return httpx.Response(200, json={"id": "agr-1", "status": "running"})
        if request.url.path.endswith("/fixtures"):
            return httpx.Response(409, json={"error": "revision_conflict"})
        return httpx.Response(404)

    with client_for(route) as client:
        with pytest.raises(TimeoutError):
            client.wait_for_agent_run("agd-1", "agr-1", timeout=0, poll_interval=0)
        with pytest.raises(httpx.HTTPStatusError) as raised:
            client.save_agent_test_cases("agd-1", [{"id": "a", "input": "x", "expected": {}}], revision=0)
        assert raised.value.response.status_code == 409


def test_event_pages_pass_the_cursor():
    def route(request: httpx.Request) -> httpx.Response:
        assert request.url.params["after"] == "4"
        return httpx.Response(200, json={"status": "running", "events": [{"seq": 5}], "next": 5, "done": False})

    with client_for(route) as client:
        page = client.agent_run_events("agd-1", "agr-1", after=4)
    assert page["next"] == 5
