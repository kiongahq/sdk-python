# Generated from kionga/_async by scripts/unasync.py. Do not edit.
from __future__ import annotations

import json
from collections.abc import Iterator, Iterable
from datetime import datetime
from typing import Any

from ..data import (
    MAX_LOG_PAGE,
    FeatureMaterialization,
    FeatureView,
    LogEntry,
    _items,
    _log_filters,
    _name,
)
from ..sse import StreamError

__all__ = ["DataAPI"]


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
            for item in listing.get("objects") or []:
                yield item
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
        params = _log_filters(project_id=project_id, run_id=run_id, pipeline_id=pipeline_id, node=node,
                              sources=sources, severities=severities, text=text, since=since, until=until)
        params.update({k: v for k, v in {"after": after, "before": before, "limit": limit,
                                         "order": "desc" if newest_first else ""}.items() if v})
        body = self._request("GET", "/api/v1/logs", params=params)
        return [LogEntry.model_validate(item) for item in _items(body)]

    def stream_logs(self, *, after: int = 0, **filters: Any) -> Iterator[LogEntry]:
        """Yield new matching entries as they are written, over server-sent events,
        until you stop iterating. Takes the same filters as ``query_logs``, and
        reconnects from the last entry if the connection drops."""
        params = _log_filters(**filters)
        if after:
            params["after"] = str(after)
        for message in self._stream("/api/v1/logs/stream", params=params):
            if message.event == "error":
                raise StreamError(f"log stream: {json.loads(message.data)}")
            if message.event == "logs":
                for item in json.loads(message.data):
                    yield LogEntry.model_validate(item)

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


