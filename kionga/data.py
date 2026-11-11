"""Feature catalog, platform object storage and logs.

Mixed into ``KiongaClient``. The feature *values* an agent reads at serving time
come from the feature gateway (``AgentMemoryClient.get_entity_features``); this
module manages the catalog that describes them.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from datetime import datetime
from typing import Any
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["DataAPI", "FeatureMaterialization", "FeatureView", "LogEntry"]

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


class DataAPI:
    """Needs ``_request`` and ``_sleep`` from the client."""

    _request: Any
    _sleep: Any

    # Feature catalog

    def list_features(self) -> list[FeatureView]:
        """The latest version of every feature view in your projects."""
        return [FeatureView.model_validate(item) for item in _items(self._request("GET", "/api/v1/features"))]

    def feature_views(self) -> list[dict[str, Any]]:
        """Feature views with serving and freshness details, as the console shows them."""
        return _items(self._request("GET", "/api/v1/features/views"))

    def feature_stores(self) -> list[dict[str, Any]]:
        return _items(self._request("GET", "/api/v1/features/stores"))

    def feature_versions(self, name: str) -> list[FeatureView]:
        body = self._request("GET", f"/api/v1/features/{_name(name)}/versions")
        return [FeatureView.model_validate(item) for item in _items(body)]

    def feature_lineage(self, name: str) -> list[FeatureMaterialization]:
        """Materializations of a feature view, newest first."""
        body = self._request("GET", f"/api/v1/features/{_name(name)}/lineage")
        return [FeatureMaterialization.model_validate(item) for item in _items(body)]

    def apply_feature_view(self, name: str, *, entity: str, fields: list[dict[str, str]], source: str,
                           tags: list[str] | None = None, ttl_seconds: int = 0, **extra: Any) -> FeatureView:
        """Register a feature view, or a new version of one. ``fields`` are ``{"name", "type"}``."""
        body = {**extra, "name": name, "entity": entity, "fields": fields, "source": source, "tags": tags or []}
        if ttl_seconds:
            body["ttl_seconds"] = ttl_seconds
        return FeatureView.model_validate(self._request("POST", "/api/v1/features", json=body))

    # Platform object storage

    def list_buckets(self) -> list[Any]:
        return self._request("GET", "/api/v1/storage/buckets").get("buckets") or []

    def list_storage_objects(self, bucket: str, *, prefix: str = "") -> dict[str, Any]:
        """``objects`` directly under ``prefix`` and the ``prefixes`` below it, like a folder."""
        return self._request("GET", "/api/v1/storage/objects", params={"bucket": bucket, "prefix": prefix})

    def walk_storage(self, bucket: str, prefix: str = "") -> Iterator[dict[str, Any]]:
        """Every object under ``prefix``, descending into each sub-prefix."""
        pending = [prefix]
        while pending:
            listing = self.list_storage_objects(bucket, prefix=pending.pop())
            yield from listing.get("objects") or []
            pending.extend(reversed(listing.get("prefixes") or []))

    def preview_storage_object(self, bucket: str, key: str) -> dict[str, Any]:
        """The start of an object, for inspection. Not a download."""
        return self._request("GET", "/api/v1/storage/object", params={"bucket": bucket, "key": key})

    # Logs

    def query_logs(self, *, project_id: str = "", run_id: str = "", pipeline_id: str = "", node: str = "",
                   sources: Iterable[str] | str = (), severities: Iterable[str] | str = (), text: str = "",
                   since: datetime | None = None, until: datetime | None = None, after: int = 0,
                   before: int = 0, limit: int = 0, newest_first: bool = False) -> list[LogEntry]:
        """One page of log entries matching every filter given."""
        params: dict[str, Any] = {
            "project_id": project_id, "run_id": run_id, "pipeline_id": pipeline_id, "node": node, "q": text,
            "source": _csv(sources, LOG_SOURCES, "sources"),
            "severity": _csv(severities, LOG_SEVERITIES, "severities"),
            "since": _rfc3339(since), "until": _rfc3339(until),
            "after": after or "", "before": before or "", "limit": limit or "",
            "order": "desc" if newest_first else "",
        }
        body = self._request("GET", "/api/v1/logs", params={k: v for k, v in params.items() if v != ""})
        return [LogEntry.model_validate(item) for item in _items(body)]

    def iter_logs(self, *, follow: bool = False, poll_interval: float = 2.0, page_size: int = 500,
                  **filters: Any) -> Iterator[LogEntry]:
        """Every matching entry, oldest first, across pages. ``follow=True`` keeps
        polling for new lines until you stop iterating."""
        if not 1 <= page_size <= MAX_LOG_PAGE:
            raise ValueError(f"page_size must be 1 to {MAX_LOG_PAGE}")
        after = filters.pop("after", 0)
        if filters.pop("newest_first", False):
            raise ValueError("iter_logs reads oldest first; use query_logs(newest_first=True) for a page")
        while True:
            page = self.query_logs(after=after, limit=page_size, **filters)
            for entry in page:
                after = max(after, entry.sequence)
                yield entry
            # Stop on an empty page, not a short one, so a page the gateway
            # trimmed can't end the read early.
            if not page:
                if not follow:
                    return
                self._sleep(poll_interval)


def _rfc3339(value: datetime | None) -> str:
    if value is None:
        return ""
    if value.tzinfo is None:
        raise ValueError("pass a timezone-aware datetime, for example datetime.now(timezone.utc)")
    return value.isoformat()
