"""Agent teams client against the contract in docs/reference/agent-teams.md."""
import json

import httpx
import pytest

import kionga
from kionga import ConflictError, PermissionDeniedError, TeamRunTimeout

META = {"owner_subject": "me", "created_at": "2026-11-01T00:00:00Z", "updated_at": "2026-11-01T00:00:00Z"}
DEFINITION = {"id": "agd-1", "project_id": "p", "revision": 3, "name": "bug-fixer", "instructions": "fix",
              "model": "llm", "tools": [{"kind": "sandbox"}], "workspace": "sandbox",
              "limits": {"max_turns": 10}, **META, "history": [{"revision": 2}]}


class Gateway:
    """Records requests and answers from a route table."""

    def __init__(self, routes):
        self.routes, self.seen = routes, []

    def __call__(self, request):
        self.seen.append(request)
        answer = self.routes[(request.method, request.url.raw_path.decode().split("?")[0])]
        return answer(request) if callable(answer) else answer

    def body(self, index=-1):
        return json.loads(self.seen[index].content)


def client(routes):
    gateway = Gateway(routes)
    c = kionga.KiongaClient("http://gateway", transport=httpx.MockTransport(gateway))
    c._sleep = lambda seconds: None
    return c, gateway


def ok(body, status=200):
    return httpx.Response(status, json=body)


def test_definitions_are_listed_read_and_created():
    c, gw = client({
        ("GET", "/api/v1/agent-definitions"): ok({"items": [DEFINITION]}),
        ("POST", "/api/v1/agent-definitions"): ok(DEFINITION, 201),
    })
    [listed] = c.list_agent_definitions(project_id="p")
    assert gw.seen[0].url.params["project_id"] == "p"
    assert (listed.id, listed.revision, listed.tools) == ("agd-1", 3, [{"kind": "sandbox"}])
    created = c.create_agent_definition("p", "bug-fixer", instructions="fix", workspace="sandbox")
    assert gw.body() == {"project_id": "p", "name": "bug-fixer", "instructions": "fix", "workspace": "sandbox"}
    assert created.owner_subject == "me", "fields the SDK doesn't model are kept"


def test_an_update_sends_only_fields_a_save_accepts_with_the_revision_it_was_based_on():
    c, gw = client({
        ("GET", "/api/v1/agent-definitions/agd-1"): ok(DEFINITION),
        ("PUT", "/api/v1/agent-definitions/agd-1"): ok({**DEFINITION, "revision": 4, "instructions": "fix fast"}),
    })
    saved = c.update_agent_definition(c.get_agent_definition("agd-1"), instructions="fix fast")
    sent = gw.body()
    assert sent["revision"] == 3 and sent["instructions"] == "fix fast"
    assert not {"id", "owner_subject", "created_at", "updated_at", "history"} & set(sent)
    assert saved.revision == 4


def test_a_stale_update_raises_a_conflict_carrying_the_latest_version():
    latest = {**DEFINITION, "revision": 5}
    c, _ = client({("PUT", "/api/v1/agent-definitions/agd-1"): ok(
        {"error": "revision_conflict", "message": "changed elsewhere", "latest": latest}, 409)})
    with pytest.raises(ConflictError) as caught:
        c.update_agent_definition(kionga.AgentDefinition.model_validate(DEFINITION), name="x")
    assert caught.value.code == "revision_conflict"
    assert caught.value.response.json()["latest"]["revision"] == 5


def test_ids_are_escaped_in_paths():
    c, gw = client({("DELETE", "/api/v1/agent-teams/a%2Fb"): httpx.Response(204)})
    c.delete_agent_team("a/b")
    assert gw.seen[0].url.raw_path == b"/api/v1/agent-teams/a%2Fb"


def test_starting_a_run_sends_one_idempotency_key_in_the_body_and_header_and_retries_safely():
    answers = iter([ok({"error": "unavailable"}, 503), ok({"id": "atr-1", "status": "running"}, 201)])
    c, gw = client({("POST", "/api/v1/team-runs"): lambda request: next(answers)})
    run = c.start_team_run("agt-1", "fix the bug")
    assert run.id == "atr-1" and not run.finished
    assert len(gw.seen) == 2, "a keyed start is safe to retry"
    keys = {gw.body(i)["idempotency_key"] for i in (0, 1)} | {r.headers["Idempotency-Key"] for r in gw.seen}
    assert len(keys) == 1 and keys.pop().startswith("sdk-")
    assert gw.body()["allow_side_effects"] is False


def test_a_given_idempotency_key_is_used_as_is():
    c, gw = client({("POST", "/api/v1/team-runs"): ok({"id": "atr-1", "status": "running"}, 201)})
    c.start_team_run("agt-1", "x", idempotency_key="job-42", allow_side_effects=True)
    assert gw.body()["idempotency_key"] == "job-42" and gw.body()["allow_side_effects"] is True


def test_a_machine_credential_is_refused_with_a_typed_error():
    c, _ = client({("POST", "/api/v1/team-runs"): ok(
        {"error": "access_denied", "message": "Team runs are started by people."}, 403)})
    with pytest.raises(PermissionDeniedError, match="started by people"):
        c.start_team_run("agt-1", "x")


def test_events_read_every_page_and_follow_until_the_log_is_complete():
    pages = iter([
        {"events": [{"seq": 1, "type": "run.started"}, {"seq": 2, "type": "worker.started"}], "done": False},
        {"events": [], "done": False},
        {"events": [{"seq": 3, "type": "run.finished"}], "done": True},
    ])
    c, gw = client({("GET", "/api/v1/team-runs/atr-1/events"): lambda request: ok(next(pages))})
    events = list(c.team_run_events("atr-1", follow=True))
    assert [e.seq for e in events] == [1, 2, 3] and events[-1].type == "run.finished"
    assert [r.url.params["after"] for r in gw.seen] == ["0", "2", "2"]


def test_events_without_follow_stop_when_nothing_new_is_left():
    c, gw = client({("GET", "/api/v1/team-runs/atr-1/events"): ok({"events": [], "done": False})})
    assert list(c.team_run_events("atr-1", after=7)) == []
    assert gw.seen[0].url.params["after"] == "7" and len(gw.seen) == 1


def test_waiting_returns_the_finished_run():
    states = iter(["running", "running", "succeeded"])
    c, gw = client({("GET", "/api/v1/team-runs/atr-1"): lambda request: ok({"id": "atr-1", "status": next(states)})})
    assert c.wait_for_team_run("atr-1").status == "succeeded" and len(gw.seen) == 3


def test_waiting_gives_up_with_the_last_state():
    c, _ = client({("GET", "/api/v1/team-runs/atr-1"): ok({"id": "atr-1", "status": "running"})})
    with pytest.raises(TeamRunTimeout) as caught:
        c.wait_for_team_run("atr-1", timeout=0)
    assert caught.value.run.status == "running"


def test_messages_cancel_resume_and_workers_use_the_documented_routes():
    c, gw = client({
        ("POST", "/api/v1/team-runs/atr-1/messages"): ok({"ok": True}),
        ("POST", "/api/v1/team-runs/atr-1/cancel"): ok({"id": "atr-1", "status": "cancelling"}),
        ("POST", "/api/v1/team-runs/atr-1/resume"): ok({"id": "atr-1", "status": "running", "resume_count": 1}),
        ("GET", "/api/v1/team-runs/atr-1/workers"): ok({"items": [{"id": "atw-1", "status": "waiting_approval", "role": "coordinator"}]}),
        ("GET", "/api/v1/team-runs"): ok({"items": [{"id": "atr-1", "status": "failed"}]}),
    })
    c.send_team_message("atr-1", "atw-1", "use the staging database")
    assert gw.body() == {"to": "atw-1", "text": "use the staging database"}
    assert c.cancel_team_run("atr-1").status == "cancelling"
    assert c.resume_team_run("atr-1").resume_count == 1
    assert c.team_run_workers("atr-1")[0].status == "waiting_approval"
    [run] = c.list_team_runs(team_id="agt-1")
    assert run.finished and gw.seen[-1].url.params["team_id"] == "agt-1"
