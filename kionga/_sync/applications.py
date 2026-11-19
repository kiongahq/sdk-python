# Generated from kionga/_async by scripts/unasync.py. Do not edit.
from __future__ import annotations

import json
import time
from collections.abc import Iterator
from datetime import datetime
from typing import Any

from ..applications import (
    Application,
    ApplicationEvent,
    ApplicationFailed,
    ApplicationLogLine,
    ApplicationsNotConfiguredError,
    ApplicationTimeout,
    GitSource,
    Scaling,
    application_body,
    failure_reason,
)
from ..data import _items, _name, _rfc3339
from ..errors import NotConfiguredError
from ..sse import StreamError

__all__ = ["ApplicationsAPI"]

BASE = "/api/v1/applications"


class ApplicationsAPI:
    """Application methods. Needs ``_request``, ``_send``, ``_stream`` and ``_sleep`` from the client.

    Every method raises ``ApplicationsNotConfiguredError`` on an install without
    applications (Compose, or Kubernetes without them enabled).
    """

    _request: Any
    _send: Any
    _stream: Any
    _sleep: Any

    def create_application(
        self,
        project_id: str,
        name: str,
        *,
        image: str | None = None,
        source: GitSource | dict[str, Any] | None = None,
        port: int = 8080,
        env: dict[str, str] | None = None,
        secret_refs: list[str] | None = None,
        cpu: str | None = None,
        memory: str | None = None,
        min_replicas: int = 1,
        max_replicas: int | None = None,
        scaling: Scaling | dict[str, Any] | None = None,
        health_path: str = "/",
        visibility: str = "sso",
    ) -> Application:
        """Deploy a service from ``image``, or from ``source`` (a Git repository Kionga builds).

        It is served at ``https://<name>.<install domain>``. ``min_replicas=0`` scales it
        to zero when idle; ``scaling`` chooses ``requests`` or ``cpu`` as the trigger.
        ``visibility="sso"`` keeps it behind Kionga sign-in; ``"public"`` opens it.
        ``secret_refs`` name Kionga secrets exposed to the container as environment.
        Raises ``ValueError`` for an invalid field before sending anything.
        """
        body = application_body(
            project_id=project_id, name=name, image=image, source=source, port=port, env=env,
            secret_refs=secret_refs, cpu=cpu, memory=memory, min_replicas=min_replicas,
            max_replicas=max_replicas, scaling=scaling, health_path=health_path,
            visibility=visibility,
        )
        return Application.model_validate(self._apps("POST", BASE, json=body))

    def list_applications(self, project_id: str = "") -> list[Application]:
        params = {"project_id": project_id} if project_id else None
        body = self._apps("GET", BASE, params=params)
        return [Application.model_validate(item) for item in _items(body)]

    def get_application(self, application_id: str) -> Application:
        return Application.model_validate(self._apps("GET", f"{BASE}/{_name(application_id)}"))

    def update_application(
        self,
        application_id: str,
        *,
        image: str | None = None,
        source: GitSource | dict[str, Any] | None = None,
        port: int | None = None,
        env: dict[str, str] | None = None,
        secret_refs: list[str] | None = None,
        cpu: str | None = None,
        memory: str | None = None,
        min_replicas: int | None = None,
        max_replicas: int | None = None,
        scaling: Scaling | dict[str, Any] | None = None,
        health_path: str | None = None,
        visibility: str | None = None,
    ) -> Application:
        """Change only the fields given; the rest stay as they are. Rolls the application."""
        body = application_body(
            image=image, source=source, port=port, env=env, secret_refs=secret_refs, cpu=cpu,
            memory=memory, min_replicas=min_replicas, max_replicas=max_replicas,
            scaling=scaling, health_path=health_path, visibility=visibility, partial=True,
        )
        return Application.model_validate(
            self._apps("PATCH", f"{BASE}/{_name(application_id)}", json=body))

    def delete_application(self, application_id: str) -> None:
        """Delete the application, its workload and its host name."""
        self._apps("DELETE", f"{BASE}/{_name(application_id)}")

    def redeploy_application(self, application_id: str) -> Application:
        """Roll the application again: rebuild a Git source, re-pull an image tag."""
        return Application.model_validate(
            self._apps("POST", f"{BASE}/{_name(application_id)}/redeploy", json={}))

    def application_events(self, application_id: str) -> list[ApplicationEvent]:
        """Kubernetes events for the application, oldest first."""
        body = self._apps("GET", f"{BASE}/{_name(application_id)}/events")
        return [ApplicationEvent.model_validate(item) for item in _items(body)]

    def application_logs(self, application_id: str, *, tail: int = 0,
                         since: datetime | None = None) -> list[ApplicationLogLine]:
        """Recent log lines from every replica. ``tail`` limits the count per replica."""
        if tail < 0:
            raise ValueError("tail can't be negative")
        params = {k: v for k, v in {"tail": str(tail) if tail else "", "since": _rfc3339(since)}.items() if v}
        response = self._apps_send("GET", f"{BASE}/{_name(application_id)}/logs",
                                   params=params or None)
        if response.status_code == 204 or not response.content:
            return []
        if "json" in response.headers.get("content-type", ""):
            return [ApplicationLogLine.model_validate(item) for item in _items(response.json())]
        return [ApplicationLogLine(message=line) for line in response.text.splitlines()]

    def stream_application_logs(self, application_id: str, *, tail: int = 0) -> Iterator[ApplicationLogLine]:
        """Yield log lines as they are written, over server-sent events, until you stop
        iterating.

        If the connection drops it reconnects from the last line's timestamp
        (``since``), skipping lines it already yielded, or with ``tail=0`` when lines
        carry no timestamp, so nothing is repeated. Lines written while it was
        disconnected are lost in that second case.
        """
        last: datetime | None = None
        seen_at_last: set[tuple[str, str, str]] = set()

        def resume() -> dict[str, str]:
            if last is not None:
                return {"follow": "true", "since": last.isoformat()}
            return {"follow": "true", "tail": "0"}

        params = {"follow": "true", **({"tail": str(tail)} if tail else {})}
        path = f"{BASE}/{_name(application_id)}/logs/stream"
        try:
            for message in self._stream(path, params=params, resume=resume):
                if message.event == "end":
                    return
                if message.event == "error":
                    raise StreamError(f"application log stream: {message.data}")
                for item in _log_items(message.data):
                    line = ApplicationLogLine.model_validate(item)
                    if line.timestamp is not None:
                        # `since` is inclusive, so a resumed stream repeats the lines
                        # of the last timestamp; drop those and anything older.
                        key = (line.pod, line.container, line.message)
                        if last is not None and _older(line.timestamp, last):
                            continue
                        if last is not None and line.timestamp == last:
                            if key in seen_at_last:
                                continue
                        else:
                            last, seen_at_last = line.timestamp, set()
                        seen_at_last.add(key)
                    yield line
        except NotConfiguredError as error:
            raise ApplicationsNotConfiguredError.from_error(error) from None

    def wait_until_ready(self, application_id: str, *, timeout: float = 600.0,
                         poll_interval: float = 3.0, generation: int | None = None) -> Application:
        """Poll until the application is ready, and return it.

        After an update or redeploy, pass the ``generation`` of the application it
        returned, so a status left over from the previous rollout is not taken as
        ready. ``Application.ready`` already ignores a status whose
        ``observed_generation`` is behind the application's ``generation``.

        Raises ``ApplicationFailed`` as soon as it fails, with the reason from its
        status, or from its latest warning event. Raises ``ApplicationTimeout`` when
        it is still starting after ``timeout`` seconds, with the latest reason known,
        such as an image that can't be pulled.
        """
        deadline = time.monotonic() + timeout
        while True:
            application = self.get_application(application_id)
            if application.ready and _caught_up(application, generation):
                return application
            if application.failed:
                raise ApplicationFailed(application, self._reason(application))
            if time.monotonic() >= deadline:
                raise ApplicationTimeout(application, timeout, self._reason(application))
            self._sleep(poll_interval)

    def _reason(self, application: Application) -> str:
        reason = failure_reason(application)
        if reason:
            return reason
        try:
            events = self.application_events(application.id)
        except Exception:  # the reason is a courtesy; never hide the real error with it
            return ""
        return failure_reason(application, events)

    def _apps(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            return self._request(method, path, **kwargs)
        except NotConfiguredError as error:
            raise ApplicationsNotConfiguredError.from_error(error) from None

    def _apps_send(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            return self._send(method, path, **kwargs)
        except NotConfiguredError as error:
            raise ApplicationsNotConfiguredError.from_error(error) from None


def _log_items(data: str) -> list[Any]:
    try:
        value = json.loads(data)
    except ValueError:
        return [line for line in data.splitlines() if line]
    if isinstance(value, dict) and "items" in value:
        value = value["items"] or []
    items = value if isinstance(value, list) else [value]
    return [item if isinstance(item, (dict, str)) else str(item) for item in items]


def _caught_up(application: Application, generation: int | None) -> bool:
    if generation is None:
        return True
    observed = application.status.observed_generation
    if observed is None:
        observed = application.generation
    return observed is not None and observed >= generation


def _older(value: datetime, than: datetime) -> bool:
    try:
        return value < than
    except TypeError:  # one is timezone-aware and the other isn't: can't order them
        return False
