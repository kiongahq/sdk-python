"""Agent teams: reusable agent definitions, teams of them, and team runs.

Mixed into ``KiongaClient``; see docs/reference/agent-teams.md for the contract.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "AgentDefinition",
    "AgentTeam",
    "TeamEvent",
    "TeamRun",
    "TeamRunTimeout",
    "TeamWorker",
]

TERMINAL_RUN_STATUSES = frozenset({"succeeded", "failed", "cancelled"})
READ_ONLY_FIELDS = frozenset({"id", "owner_subject", "created_at", "updated_at", "history"})


class _Open(BaseModel):
    # Keep fields the SDK doesn't model yet, so nothing the gateway sends is lost.
    model_config = ConfigDict(extra="allow")


class AgentDefinition(_Open):
    id: str
    project_id: str
    revision: int
    name: str
    description: str = ""
    instructions: str = ""
    model: str = ""
    tools: list[dict[str, Any]] = Field(default_factory=list)
    workspace: str = "none"
    limits: dict[str, int] = Field(default_factory=dict)


class AgentTeam(_Open):
    id: str
    project_id: str
    revision: int
    name: str
    description: str = ""
    coordinator: dict[str, Any] = Field(default_factory=dict)
    members: list[str] = Field(default_factory=list)
    brief: str = ""
    limits: dict[str, int] = Field(default_factory=dict)


class TeamRun(_Open):
    id: str
    team_id: str = ""
    project_id: str = ""
    status: str
    usage: dict[str, Any] = Field(default_factory=dict)
    resume_count: int = 0

    @property
    def finished(self) -> bool:
        return self.status in TERMINAL_RUN_STATUSES


class TeamWorker(_Open):
    id: str
    status: str
    role: str = ""
    agent: str = ""
    task: str = ""
    report: Any = None
    last_error: Any = None


class TeamEvent(_Open):
    seq: int
    type: str


class TeamRunTimeout(TimeoutError):
    """The run was still going when ``wait_for_team_run`` gave up. ``run`` is its last state."""

    def __init__(self, run: TeamRun, timeout: float) -> None:
        super().__init__(f"team run {run.id} still {run.status} after {timeout:g}s")
        self.run = run


def _path(*parts: str) -> str:
    return "/".join(quote(part, safe="") for part in parts)
