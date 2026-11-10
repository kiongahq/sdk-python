"""Against a running stack: KIONGA_LIVE_URL=http://127.0.0.1:8080 pytest tests/test_live_gateway.py

Signs in as the local break-glass admin (KIONGA_LIVE_USER / KIONGA_LIVE_PASSWORD),
because team runs are started by people, and cleans up what it creates. Skipped
unless KIONGA_LIVE_URL is set.
"""
import os
import time

import httpx
import pytest

import kionga

BASE = os.environ.get("KIONGA_LIVE_URL", "")
pytestmark = pytest.mark.skipif(not BASE, reason="set KIONGA_LIVE_URL to run against a gateway")


@pytest.fixture
def client():
    login = httpx.post(f"{BASE}/auth/local/login", headers={"Origin": BASE}, data={
        "username": os.environ.get("KIONGA_LIVE_USER", "admin"),
        "password": os.environ.get("KIONGA_LIVE_PASSWORD", "kionga-local")})
    assert login.status_code in (200, 302, 303), login.text
    c = kionga.KiongaClient(BASE)
    c._client.cookies = login.cookies
    c._client.headers["Origin"] = BASE
    yield c
    c.close()


def test_an_agent_team_runs_end_to_end(client):
    projects = client.list_projects()
    if not projects:
        pytest.skip("the gateway has no project to run in")
    name = f"sdk-live-{time.time_ns()}"
    created = []
    try:
        definition = client.create_agent_definition(
            projects[0].id, name, description="checks", instructions="Reply done.", model="llm",
            limits={"max_turns": 3})
        created.append((client.delete_agent_definition, definition.id))
        updated = client.update_agent_definition(definition, description="checks carefully")
        assert updated.revision == definition.revision + 1
        with pytest.raises(kionga.ConflictError) as stale:
            client.update_agent_definition(definition, description="stale")
        assert stale.value.code == "revision_conflict"

        team = client.create_agent_team(projects[0].id, name, [definition.id], coordinator={
            "instructions": "Spawn the member once, then finish.", "model": "llm"})
        created.insert(0, (client.delete_agent_team, team.id))

        run = client.start_team_run(team.id, "say done", idempotency_key=name)
        assert client.start_team_run(team.id, "say done", idempotency_key=name).id == run.id
        final = client.wait_for_team_run(run.id, timeout=300)
        events = list(client.team_run_events(run.id))
        assert final.finished and events and events[-1].type == "run.finished"
        assert [e.seq for e in events] == sorted(e.seq for e in events)

        with pytest.raises(kionga.NotFoundError):
            client.get_team_run("atr-does-not-exist")
    finally:
        for delete, resource_id in created:
            try:
                delete(resource_id)
            except kionga.KiongaAPIError:
                pass
