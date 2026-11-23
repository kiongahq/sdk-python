# Generated from kionga/_async by scripts/unasync.py. Do not edit.
from __future__ import annotations

import time
from collections.abc import Iterator, Callable
from datetime import datetime
from typing import Any

from ..applications import (
    MAX_LOG_TAIL,
    Application,
    ApplicationEvent,
    ApplicationFailed,
    ApplicationLogLine,
    ApplicationPodLogs,
    ApplicationsNotConfiguredError,
    ApplicationTimeout,
    SLICE_END,
    GitSource,
    LogCursor,
    Scaling,
    application_body,
    end_reason,
    failure_reason,
    log_line,
    log_stream_params,
)
from ..data import _items, _name
from ..errors import NotConfiguredError

__all__ = ["ApplicationsAPI"]

BASE = "/api/v1/applications"


class ApplicationsAPI:
    """Application methods. Needs ``_request`` and ``_sleep`` from the client.

    Applications are addressed by name. Every method raises
    ``ApplicationsNotConfiguredError`` on an install without applications
    (Compose, or Kubernetes without a base domain).
    """

    _request: Any
    _sleep: Any

    def create_application(
        self,
        project_id: str,
        name: str,
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
        """Deploy a service from ``image``, or from ``source`` (a Git repository Kionga builds).

        It is served at ``https://<name>.<install domain>``. ``min_replicas=0`` scales it
        to zero when idle (with ``requests`` scaling); ``scaling`` chooses ``requests``
        (concurrent requests per replica) or ``cpu`` (utilisation percent).
        ``visibility="sso"`` keeps it behind Kionga sign-in; ``"public"`` opens it.
        ``secret_refs`` name Kubernetes secrets exposed to the container as environment.
        Fields left out take the gateway's defaults: port 8080, 250m CPU, 256Mi
        memory, 1 to 3 replicas, requests scaling, health path ``/``, ``sso``.
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

    def get_application(self, name: str) -> Application:
        """One application with its live status, URL, conditions and last build."""
        return Application.model_validate(self._apps("GET", f"{BASE}/{_name(name)}"))

    def update_application(
        self,
        name: str,
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
        """Change the fields given and keep the rest; the operator rolls it out.

        The gateway replaces the whole configuration, so this reads the current one,
        applies your changes and sends the result. A change someone saves between
        the read and the write is overwritten. Giving ``image`` drops a Git
        ``source`` and the reverse. ``env`` and ``secret_refs`` replace the lists.
        """
        current = self.get_application(name)
        merged = current.request()
        resources = dict(merged.get("resources") or {})
        replicas = dict(merged.get("replicas") or {})
        changes = {"port": port, "env": env, "secret_refs": secret_refs, "health_path": health_path,
                   "visibility": visibility, "scaling": scaling}
        if not any(value is not None for value in (image, source, cpu, memory, min_replicas, max_replicas,
                                                   *changes.values())):
            raise ValueError("nothing to update")
        if image and source is not None:
            raise ValueError("give image or source, not both")
        if image:
            merged.pop("source", None)
            merged["image"] = image
        if source is not None:
            merged.pop("image", None)
            merged["source"] = source
        merged.update({key: value for key, value in changes.items() if value is not None})
        body = application_body(
            project_id=merged.get("project_id") or None, name=current.name, image=merged.get("image"),
            source=merged.get("source"), port=merged.get("port"), env=merged.get("env"),
            secret_refs=merged.get("secret_refs"), cpu=cpu or resources.get("cpu"),
            memory=memory or resources.get("memory"),
            min_replicas=replicas.get("min") if min_replicas is None else min_replicas,
            max_replicas=replicas.get("max") if max_replicas is None else max_replicas,
            scaling=merged.get("scaling"), health_path=merged.get("health_path"),
            visibility=merged.get("visibility"),
        )
        return Application.model_validate(self._apps("PUT", f"{BASE}/{_name(name)}", json=body))

    def delete_application(self, name: str) -> None:
        """Delete the application, its workload and its host name."""
        self._apps("DELETE", f"{BASE}/{_name(name)}")

    def redeploy_application(self, name: str) -> Application:
        """Restart the application's pods, and rebuild a Git source."""
        return Application.model_validate(self._apps("POST", f"{BASE}/{_name(name)}/redeploy", json={}))

    def application_events(self, name: str) -> list[ApplicationEvent]:
        """Kubernetes events for the application, its pods and replica sets, newest first."""
        body = self._apps("GET", f"{BASE}/{_name(name)}/events")
        return [ApplicationEvent.model_validate(item) for item in _items(body)]

    def application_logs(self, name: str, *, tail: int | None = None,
                         container: str = "") -> list[ApplicationPodLogs]:
        """Recent logs of each pod: the last ``tail`` lines (1 to 2000; the gateway
        defaults to 200) of ``container``, or of each pod's first container."""
        params: dict[str, str] = {}
        if tail is not None:
            if not 1 <= tail <= MAX_LOG_TAIL:
                raise ValueError(f"tail must be 1 to {MAX_LOG_TAIL}")
            params["tail"] = str(tail)
        if container:
            params["container"] = container
        body = self._apps("GET", f"{BASE}/{_name(name)}/logs", params=params or None)
        return [ApplicationPodLogs.model_validate(item) for item in _items(body)]

    def follow_application_logs(
        self, name: str, *, tail: int | None = None, container: str = "",
        since: str | datetime | None = None, max_seconds: int | None = None,
        on_end: Callable[[str], None] | None = None,
    ) -> Iterator[ApplicationLogLine]:
        """Yield log lines as the application's pods write them (Server-Sent Events).

        Starts with the last ``tail`` lines (1 to 2000, default 200) of each pod, or
        with the lines after ``since`` (an RFC 3339 timestamp or a timezone-aware
        datetime). The gateway streams at most 20 pods and closes the stream after
        ``max_seconds`` (1 to 3600, default 600); this reopens it from the last line
        delivered, so a long follow has no gaps and no repeats, and identical lines
        are all delivered. Dropped connections are retried with the client's policy.

        The iterator ends when the pods' logs finish (no pods are running, or every
        log stream closed, such as after a rollout); ``on_end`` then receives the
        gateway's reason. Pods that start later are picked up by following again
        with ``since`` set to the last ``line.time``. Stop early by leaving the loop.
        """
        params = log_stream_params(tail=tail, container=container, since=since, max_seconds=max_seconds)
        cursor = LogCursor()
        stream = self._stream(f"{BASE}/{_name(name)}/logs", params=params, resume=cursor.resume,
                              on_connect=cursor.connected)
        try:
            for event in stream:
                if event.event == "end":
                    reason = end_reason(event)
                    if reason.startswith(SLICE_END):
                        continue  # the time slice is used up: the stream reopens
                    if on_end:
                        on_end(reason)
                    return
                if event.event != "log":
                    continue
                line = log_line(event)
                if cursor.accept(line):
                    yield line
        except NotConfiguredError as error:
            raise ApplicationsNotConfiguredError.from_error(error) from None
        finally:
            stream.close()

    def wait_until_ready(self, name: str, *, timeout: float = 600.0,
                         poll_interval: float = 3.0, generation: int | None = None) -> Application:
        """Poll until the application is ready, and return it.

        Raises ``ApplicationFailed`` as soon as it fails, with the reason from its
        status, or from its latest warning event. Raises ``ApplicationTimeout`` when
        it is still starting after ``timeout`` seconds, with the latest reason known,
        such as an image that can't be pulled.

        After an update or redeploy, pass the ``generation`` it returned so the
        previous rollout's status is not taken as ready. That needs the gateway to
        report generations; without them the phase decides.
        """
        deadline = time.monotonic() + timeout
        while True:
            application = self.get_application(name)
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
            events = self.application_events(application.name)
        except Exception:  # the reason is a courtesy; never hide the real error with it
            return ""
        return failure_reason(application, events)

    def _apps(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            return self._request(method, path, **kwargs)
        except NotConfiguredError as error:
            raise ApplicationsNotConfiguredError.from_error(error) from None


def _caught_up(application: Application, generation: int | None) -> bool:
    if generation is None:
        return True
    observed = application.status.observed_generation
    if observed is None:
        observed = application.generation
    # A gateway that reports no generation can't tell rollouts apart: fall back to
    # the phase rather than waiting until the timeout.
    return observed is None or observed >= generation
