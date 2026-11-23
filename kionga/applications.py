"""Applications: your own services, run by Kionga on Kubernetes.

An application is a container image, or a Git repository Kionga builds, served at
``https://<name>.<install domain>``. Kionga keeps it running, restarts it when its
health check fails and scales it between ``min_replicas`` and ``max_replicas``
(``min_replicas=0`` scales it to zero when idle).

Applications need a Kubernetes install. A Compose install answers ``not_configured``,
which the client raises as ``ApplicationsNotConfiguredError``.

Mixed into ``KiongaClient``. This module holds what both clients share: response
models, errors and the request validation.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .errors import ConflictError, NotConfiguredError, ServerError
from .models import Scaling

__all__ = [
    "Application",
    "ApplicationCondition",
    "ApplicationEvent",
    "ApplicationFailed",
    "ApplicationLogLine",
    "ApplicationStatus",
    "ApplicationTimeout",
    "ApplicationsNotConfiguredError",
    "GitSource",
    "Scaling",
]

# A name becomes the first label of the application's host name, so it must be a
# DNS label: lower case letters, digits and hyphens, starting with a letter.
NAME = re.compile(r"^[a-z]([-a-z0-9]{0,61}[a-z0-9])?$")
ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SCALING_METRICS = ("requests", "cpu")
VISIBILITIES = ("public", "sso")
# Phases the operator reports. Anything else counts as still in progress.
READY_PHASES = frozenset({"ready", "running"})
FAILED_PHASES = frozenset({"failed"})

ENABLE_HINT = (
    "Applications run only on a Kubernetes install of Kionga. This install runs on "
    "Compose, or Kubernetes without applications enabled. Install Kionga with the "
    "Helm chart and set applications.baseDomain to turn them on."
)


class _Open(BaseModel):
    # Keep fields the SDK doesn't model yet, so nothing the gateway sends is lost.
    model_config = ConfigDict(extra="allow")


class GitSource(_Open):
    """Build the application from a Git repository instead of a prebuilt image."""

    git: str
    ref: str = ""
    context_dir: str = ""
    dockerfile: str = ""


class ApplicationCondition(_Open):
    type: str
    status: str = ""
    reason: str = ""
    message: str = ""
    last_transition_time: datetime | None = None


class ApplicationStatus(_Open):
    phase: str = ""
    url: str = ""
    ready_replicas: int = 0
    observed_generation: int | None = None
    message: str = ""
    last_build: dict[str, Any] | None = None
    conditions: list[ApplicationCondition] = Field(default_factory=list)

    @field_validator("conditions", mode="before")
    @classmethod
    def empty_conditions(cls, value: object) -> object:
        return [] if value is None else value


class Application(_Open):
    id: str
    name: str
    project_id: str = ""
    image: str = ""
    source: GitSource | None = None
    port: int = 0
    env: dict[str, str] = Field(default_factory=dict)
    secret_refs: list[str] = Field(default_factory=list)
    resources: dict[str, Any] = Field(default_factory=dict)
    replicas: dict[str, int] = Field(default_factory=dict)
    scaling: Scaling | None = None
    health_path: str = ""
    visibility: str = ""
    generation: int | None = None
    status: ApplicationStatus = Field(default_factory=ApplicationStatus)
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @model_validator(mode="before")
    @classmethod
    def _status_shapes(cls, value: Any) -> Any:
        # The status may arrive nested ({"status": {"phase": ...}}), as a bare
        # phase string, or flattened onto the application. Accept all three.
        if not isinstance(value, dict):
            return value
        value = dict(value)
        status = value.get("status")
        if isinstance(status, str):
            status = {"phase": status}
        elif not isinstance(status, dict):
            status = {}
        for key in ("phase", "url", "ready_replicas", "observed_generation", "message", "last_build",
                    "conditions"):
            if key in value and key not in status:
                status[key] = value[key]
        value["status"] = status
        for key in ("env", "resources", "replicas"):
            if value.get(key) is None:
                value.pop(key, None)
        if value.get("secret_refs") is None:
            value.pop("secret_refs", None)
        return value

    @property
    def phase(self) -> str:
        return self.status.phase

    @property
    def url(self) -> str:
        return self.status.url

    @property
    def rolled_out(self) -> bool:
        """False while the operator has not yet acted on the latest spec, when the
        gateway reports ``generation`` and ``status.observed_generation``."""
        observed = self.status.observed_generation
        return self.generation is None or observed is None or observed >= self.generation

    @property
    def ready(self) -> bool:
        """True when the latest spec serves requests. A scaled-to-zero application is
        ready with no running replicas: the first request starts one. A status left
        over from the previous rollout does not count (see ``rolled_out``)."""
        if not self.rolled_out:
            return False
        if self.status.phase.lower() in READY_PHASES:
            return True
        return any(c.type == "Ready" and c.status == "True" for c in self.status.conditions)

    @property
    def failed(self) -> bool:
        return self.status.phase.lower() in FAILED_PHASES

    @property
    def failure_reason(self) -> str:
        """The clearest explanation the status carries, or ``""``."""
        return failure_reason(self)


class ApplicationEvent(_Open):
    """A Kubernetes event about the application, such as a failed image pull."""

    type: str = ""
    reason: str = ""
    message: str = ""
    object: str = ""
    count: int = 0
    timestamp: datetime | None = None

    @model_validator(mode="before")
    @classmethod
    def _timestamp(cls, value: Any) -> Any:
        if isinstance(value, dict) and "timestamp" not in value:
            for key in ("last_seen", "last_timestamp", "ts", "created_at", "first_seen"):
                if value.get(key):
                    return {**value, "timestamp": value[key]}
        return value


class ApplicationLogLine(_Open):
    message: str
    timestamp: datetime | None = None
    pod: str = ""
    container: str = ""

    @model_validator(mode="before")
    @classmethod
    def _shapes(cls, value: Any) -> Any:
        if isinstance(value, str):
            return {"message": value}
        if isinstance(value, dict):
            value = dict(value)
            if "message" not in value:
                for key in ("line", "log", "text"):
                    if key in value:
                        value["message"] = value[key]
                        break
            if "timestamp" not in value and value.get("ts"):
                value["timestamp"] = value["ts"]
        return value


class LogReplayFilter:
    """Drops the lines a resumed log stream sends again.

    A resumed stream asks for lines ``since`` the last timestamp seen, inclusive,
    so the gateway repeats the lines of that timestamp. Only those repeats are
    dropped, counted per pod, container and message, so identical lines written
    in the same instant still all come through. Live lines are never filtered.
    Timestamps without a timezone are taken as UTC, as Kubernetes writes them.
    """

    def __init__(self) -> None:
        self.last: datetime | None = None
        self.at_last: Counter[tuple[str, str, str]] = Counter()
        self.replay: Counter[tuple[str, str, str]] | None = None

    def resume_params(self) -> dict[str, str]:
        """The query for a reconnect, and the start of replay filtering."""
        if self.last is None:
            self.replay = None
            return {"follow": "true", "tail": "0"}
        self.replay = Counter(self.at_last)
        return {"follow": "true", "since": self.last.isoformat()}

    def accept(self, line: ApplicationLogLine) -> bool:
        if line.timestamp is None:
            return True
        ts = _utc(line.timestamp)
        key = (line.pod, line.container, line.message)
        if self.replay is not None and self.last is not None:
            if ts < self.last:
                return False
            if ts == self.last and self.replay[key] > 0:
                self.replay[key] -= 1
                return False
            if ts > self.last:
                self.replay = None
        if self.last is None or ts > self.last:
            self.last, self.at_last = ts, Counter()
        if ts == self.last:
            self.at_last[key] += 1
        return True


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class ApplicationsNotConfiguredError(NotConfiguredError):
    """This install can't run applications: they need Kionga on Kubernetes.

    Like ``NotConfiguredError``, the error raised is also the class for its status
    (``ConflictError`` for the gateway's 409). Build one with ``from_error``.
    """

    def __init__(self, response: Any) -> None:
        super().__init__(response)
        gateway = f" Gateway: {self.message}" if self.message else ""
        self.args = (f"{ENABLE_HINT}{gateway}",)
        self.hint = ENABLE_HINT

    @staticmethod
    def from_error(error: NotConfiguredError) -> ApplicationsNotConfiguredError:
        if isinstance(error, ConflictError):
            return _ConflictApplicationsNotConfigured(error.response)
        if isinstance(error, ServerError):
            return _ServerApplicationsNotConfigured(error.response)
        return ApplicationsNotConfiguredError(error.response)


class _ConflictApplicationsNotConfigured(ApplicationsNotConfiguredError, ConflictError):
    pass


class _ServerApplicationsNotConfigured(ApplicationsNotConfiguredError, ServerError):
    pass


class ApplicationFailed(Exception):
    """The application failed to start. ``application`` is its last state and
    ``reason`` the explanation from its status or events."""

    def __init__(self, application: Application, reason: str) -> None:
        super().__init__(f"application {application.name} failed: {reason or 'no reason given'}")
        self.application = application
        self.reason = reason


class ApplicationTimeout(TimeoutError):
    """The application was not ready when ``wait_until_ready`` gave up."""

    def __init__(self, application: Application, timeout: float, reason: str = "") -> None:
        text = f"application {application.name} still {application.phase or 'pending'} after {timeout:g}s"
        super().__init__(f"{text}: {reason}" if reason else text)
        self.application = application
        self.reason = reason


def failure_reason(application: Application, events: list[ApplicationEvent] | None = None) -> str:
    """Pick the most specific reason the status (or, failing that, a warning event) gives."""
    status = application.status
    if status.message:
        return status.message
    for condition in status.conditions:
        if condition.status == "False" and (condition.message or condition.reason):
            return ": ".join(part for part in (condition.reason, condition.message) if part)
    build = status.last_build or {}
    if str(build.get("status", build.get("phase", ""))).lower() == "failed":
        return f"build failed: {build.get('message') or build.get('error') or 'see build logs'}"
    for event in reversed(events or []):
        if event.type == "Warning":
            return ": ".join(part for part in (event.reason, event.message) if part)
    return ""


def application_body(
    *,
    project_id: str | None = None,
    name: str | None = None,
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
    partial: bool = False,
) -> dict[str, Any]:
    """Validate the fields given and build the request body.

    A create (``partial=False``) needs a project, a name and exactly one of
    ``image`` or ``source``. An update sends only the fields given. Raises
    ``ValueError`` before anything is sent; the gateway still validates too.
    """
    body: dict[str, Any] = {}
    if not partial:
        if not project_id:
            raise ValueError("project_id is required")
        if not name:
            raise ValueError("name is required")
        if bool(image) == (source is not None):
            raise ValueError("give exactly one of image or source")
    elif image and source is not None:
        raise ValueError("give image or source, not both")
    if project_id:
        body["project_id"] = project_id
    if name is not None:
        if not NAME.match(name):
            raise ValueError(
                f"name {name!r} must be a DNS label: lower case letters, digits and hyphens, "
                "starting with a letter, at most 63 characters")
        body["name"] = name
    if image:
        body["image"] = image
    if source is not None:
        git = source if isinstance(source, GitSource) else GitSource.model_validate(source)
        if not git.git:
            raise ValueError("source needs a git repository URL")
        body["source"] = git.model_dump(exclude_defaults=True)
    if port is not None:
        if not 1 <= port <= 65535:
            raise ValueError("port must be 1 to 65535")
        body["port"] = port
    if env is not None:
        bad = sorted(key for key in env if not ENV_NAME.match(key))
        if bad:
            raise ValueError(f"invalid environment variable names: {bad}")
        body["env"] = {key: str(value) for key, value in env.items()}
    if secret_refs is not None:
        body["secret_refs"] = list(secret_refs)
    resources = {key: value for key, value in (("cpu", cpu), ("memory", memory)) if value}
    if resources:
        body["resources"] = resources
    replicas = _replicas(min_replicas, max_replicas, partial)
    if replicas:
        body["replicas"] = replicas
    if scaling is not None:
        rule = scaling if isinstance(scaling, Scaling) else Scaling.model_validate(scaling)
        body["scaling"] = {"metric": rule.metric, "target": rule.target}
    if health_path is not None:
        if not health_path.startswith("/"):
            raise ValueError("health_path must start with /")
        body["health_path"] = health_path
    if visibility is not None:
        if visibility not in VISIBILITIES:
            raise ValueError(f"visibility must be one of {list(VISIBILITIES)}")
        body["visibility"] = visibility
    if partial and not body:
        raise ValueError("nothing to update")
    return body


def _replicas(minimum: int | None, maximum: int | None, partial: bool) -> dict[str, int]:
    if minimum is None and maximum is None:
        return {}
    if minimum is not None and minimum < 0:
        raise ValueError("min_replicas can't be negative; 0 scales to zero")
    if maximum is not None and maximum < 1:
        raise ValueError("max_replicas must be at least 1")
    if minimum is not None and maximum is not None and maximum < minimum:
        raise ValueError("max_replicas must be at least min_replicas")
    if not partial and maximum is None:
        maximum = max(minimum or 0, 1)
    return {key: value for key, value in (("min", minimum), ("max", maximum)) if value is not None}
