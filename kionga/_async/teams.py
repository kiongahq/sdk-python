# Async source of truth. kionga/_sync is generated from this by scripts/unasync.py.
from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

from pydantic import BaseModel

from ..teams import (
    READ_ONLY_FIELDS,
    AgentDefinition,
    AgentTeam,
    TeamEvent,
    TeamRun,
    TeamRunTimeout,
    TeamWorker,
    _path,
)

__all__ = ["TeamsAPI"]


class TeamsAPI:
    """Agent definition, team and team run methods. Needs ``_request`` from the client."""

    _request: Any
    _sleep: Any

    # Agent definitions

    async def list_agent_definitions(self, project_id: str = "") -> list[AgentDefinition]:
        params = {"project_id": project_id} if project_id else None
        items = await self._request("GET", "/api/v1/agent-definitions", params=params)["items"]
        return [AgentDefinition.model_validate(item) for item in items]

    async def get_agent_definition(self, definition_id: str) -> AgentDefinition:
        return AgentDefinition.model_validate(
            await self._request("GET", f"/api/v1/agent-definitions/{_path(definition_id)}"))

    async def create_agent_definition(self, project_id: str, name: str, **fields: Any) -> AgentDefinition:
        """Create an agent. ``fields`` are the documented content fields, such as ``instructions``."""
        body = {**fields, "project_id": project_id, "name": name}
        return AgentDefinition.model_validate(await self._request("POST", "/api/v1/agent-definitions", json=body))

    async def update_agent_definition(self, definition: AgentDefinition, **changes: Any) -> AgentDefinition:
        """Save changes on top of ``definition``. Raises ``ConflictError`` if someone saved since."""
        return AgentDefinition.model_validate(
            await self._save(f"/api/v1/agent-definitions/{_path(definition.id)}", definition, changes))

    async def delete_agent_definition(self, definition_id: str) -> None:
        await self._request("DELETE", f"/api/v1/agent-definitions/{_path(definition_id)}")

    async def agent_definition_revisions(self, definition_id: str) -> list[dict[str, Any]]:
        return await self._request("GET", f"/api/v1/agent-definitions/{_path(definition_id)}/revisions")["items"]

    # Teams

    async def list_agent_teams(self, project_id: str = "") -> list[AgentTeam]:
        params = {"project_id": project_id} if project_id else None
        items = await self._request("GET", "/api/v1/agent-teams", params=params)["items"]
        return [AgentTeam.model_validate(item) for item in items]

    async def get_agent_team(self, team_id: str) -> AgentTeam:
        return AgentTeam.model_validate(await self._request("GET", f"/api/v1/agent-teams/{_path(team_id)}"))

    async def create_agent_team(self, project_id: str, name: str, members: list[str], **fields: Any) -> AgentTeam:
        body = {**fields, "project_id": project_id, "name": name, "members": members}
        return AgentTeam.model_validate(await self._request("POST", "/api/v1/agent-teams", json=body))

    async def update_agent_team(self, team: AgentTeam, **changes: Any) -> AgentTeam:
        """Save changes on top of ``team``. Raises ``ConflictError`` if someone saved since."""
        return AgentTeam.model_validate(await self._save(f"/api/v1/agent-teams/{_path(team.id)}", team, changes))

    async def delete_agent_team(self, team_id: str) -> None:
        await self._request("DELETE", f"/api/v1/agent-teams/{_path(team_id)}")

    async def agent_team_revisions(self, team_id: str) -> list[dict[str, Any]]:
        return await self._request("GET", f"/api/v1/agent-teams/{_path(team_id)}/revisions")["items"]

    # Team runs

    async def start_team_run(self, team_id: str, input: str, *, allow_side_effects: bool = False,
                       idempotency_key: str = "") -> TeamRun:
        """Start a run. The key makes a retried start return the same run, so one is
        generated when none is given; pass your own to dedupe across processes."""
        key = idempotency_key or f"sdk-{uuid.uuid4()}"
        body = {"team_id": team_id, "input": input, "allow_side_effects": allow_side_effects,
                "idempotency_key": key}
        return TeamRun.model_validate(
            await self._request("POST", "/api/v1/team-runs", json=body, headers={"Idempotency-Key": key}))

    async def list_team_runs(self, team_id: str = "", project_id: str = "") -> list[TeamRun]:
        params = {k: v for k, v in {"team_id": team_id, "project_id": project_id}.items() if v}
        items = await self._request("GET", "/api/v1/team-runs", params=params or None)["items"]
        return [TeamRun.model_validate(item) for item in items]

    async def get_team_run(self, run_id: str) -> TeamRun:
        return TeamRun.model_validate(await self._request("GET", f"/api/v1/team-runs/{_path(run_id)}"))

    async def team_run_workers(self, run_id: str) -> list[TeamWorker]:
        items = await self._request("GET", f"/api/v1/team-runs/{_path(run_id)}/workers")["items"]
        return [TeamWorker.model_validate(item) for item in items]

    async def get_team_worker(self, run_id: str, worker_id: str) -> TeamWorker:
        return TeamWorker.model_validate(
            await self._request("GET", f"/api/v1/team-runs/{_path(run_id)}/workers/{_path(worker_id)}"))

    async def send_team_message(self, run_id: str, to: str, text: str) -> dict[str, Any]:
        """Message a worker (or the coordinator) as a person."""
        return await self._request("POST", f"/api/v1/team-runs/{_path(run_id)}/messages", json={"to": to, "text": text})

    async def cancel_team_run(self, run_id: str) -> TeamRun:
        return TeamRun.model_validate(await self._request("POST", f"/api/v1/team-runs/{_path(run_id)}/cancel", json={}))

    async def resume_team_run(self, run_id: str) -> TeamRun:
        """Requeue failed workers after you fix the cause."""
        return TeamRun.model_validate(await self._request("POST", f"/api/v1/team-runs/{_path(run_id)}/resume", json={}))

    async def team_run_events(self, run_id: str, *, after: int = 0, follow: bool = False,
                        poll_interval: float = 1.0) -> AsyncIterator[TeamEvent]:
        """Yield events in order after ``after``, reading every page.

        With ``follow=True`` it keeps polling until the gateway reports the log
        complete, so the final ``run.finished`` event is never missed.
        """
        path = f"/api/v1/team-runs/{_path(run_id)}/events"
        while True:
            page = await self._request("GET", path, params={"after": after})
            events = page.get("events") or []
            for item in events:
                event = TeamEvent.model_validate(item)
                after = max(after, event.seq)
                yield event
            if page.get("done") or (not follow and not events):
                return
            if not events:
                await self._sleep(poll_interval)

    async def stream_team_run_events(self, run_id: str, *, after: int = 0) -> AsyncIterator[TeamEvent]:
        """Yield events as they happen, over server-sent events, until the run ends.

        The gateway closes the stream after five minutes; it is reopened from the
        last event, so a long run is followed without gaps or repeats.
        """
        path = f"/api/v1/team-runs/{_path(run_id)}/events/stream"
        async for message in self._stream(path, params={"after": after}):
            if message.event == "end":
                return
            if message.event == "run":
                yield TeamEvent.model_validate(json.loads(message.data))

    async def wait_for_team_run(self, run_id: str, *, timeout: float = 3600.0, poll_interval: float = 2.0) -> TeamRun:
        """Poll until the run succeeds, fails or is cancelled. Raises ``TeamRunTimeout``."""
        deadline = time.monotonic() + timeout
        while True:
            run = await self.get_team_run(run_id)
            if run.finished:
                return run
            if time.monotonic() >= deadline:
                raise TeamRunTimeout(run, timeout)
            await self._sleep(poll_interval)

    async def _save(self, path: str, current: BaseModel, changes: dict[str, Any]) -> dict[str, Any]:
        # A save accepts content fields plus project_id and revision; the gateway
        # rejects the metadata it adds when reading.
        body = current.model_dump(exclude=READ_ONLY_FIELDS)
        body.update(changes)
        body["revision"] = current.revision  # type: ignore[attr-defined]
        return await self._request("PUT", path, json=body)
