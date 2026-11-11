"""Feature catalog, platform object storage and logs.

Mixed into ``KiongaClient``. The feature *values* an agent reads at serving time
come from the feature gateway (``AgentMemoryClient.get_entity_features``); this
module manages the catalog that describes them.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field


__all__ = ["FeatureMaterialization", "FeatureView", "LogEntry"]

LOG_SOURCES = frozenset({"runner", "engine", "platform", "k8s", "audit"})
MAX_LOG_PAGE = 1000  # the gateway resets a larger limit to its default of 200
LOG_SEVERITIES = frozenset({"debug", "info", "warn", "error"})


class _Open(BaseModel):
    model_config = ConfigDict(extra="allow")


class FeatureView(_Open):
    """One version of a feature view: an entity key and typed fields from a source."""

    name: str
    id: str = ""
    version: int = 0
    sha256: str = ""
    entity: str = ""
    fields: list[dict[str, Any]] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    source: str = ""
    ttl_seconds: int = 0


class FeatureMaterialization(_Open):
    """A run that wrote a feature view's values, with where they landed."""

    id: str
    view: str = ""
    view_version: int = 0
    run_id: str = ""
    source_dataset: str = ""
    offline_uri: str = ""
    entity_count: int = 0
    status: str = ""
    created_at: datetime | None = None


class LogEntry(_Open):
    sequence: int
    ts: datetime
    source: str
    severity: str
    message: str
    project_id: str = ""
    pipeline_id: str = ""
    run_id: str = ""
    node: str = ""
    attempt: int = 0
    context: dict[str, Any] = Field(default_factory=dict)


def _name(value: str) -> str:
    return quote(value, safe="")


def _items(body: Any) -> list[dict[str, Any]]:
    return body.get("items") or [] if isinstance(body, dict) else body


def _csv(values: Iterable[str] | str, allowed: frozenset[str], label: str) -> str:
    chosen = [values] if isinstance(values, str) else list(values)
    unknown = sorted(set(chosen) - allowed)
    if unknown:
        # The gateway drops unknown values silently, which would widen the query.
        raise ValueError(f"unknown {label} {unknown}; use {sorted(allowed)}")
    return ",".join(chosen)


def _log_filters(*, project_id: str = "", run_id: str = "", pipeline_id: str = "", node: str = "",
                 sources: Iterable[str] | str = (), severities: Iterable[str] | str = (), text: str = "",
                 since: datetime | None = None, until: datetime | None = None) -> dict[str, str]:
    params = {
        "project_id": project_id, "run_id": run_id, "pipeline_id": pipeline_id, "node": node, "q": text,
        "source": _csv(sources, LOG_SOURCES, "sources"),
        "severity": _csv(severities, LOG_SEVERITIES, "severities"),
        "since": _rfc3339(since), "until": _rfc3339(until),
    }
    return {k: v for k, v in params.items() if v}


def _rfc3339(value: datetime | None) -> str:
    if value is None:
        return ""
    if value.tzinfo is None:
        raise ValueError("pass a timezone-aware datetime, for example datetime.now(timezone.utc)")
    return value.isoformat()
