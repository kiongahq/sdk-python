"""Applications: your own services, run by Kionga on Kubernetes.

An application is a container image, or a Git repository Kionga builds, served at
``https://<name>.<install domain>``. Kionga keeps it running, restarts it when its
health check fails and scales it between ``min_replicas`` and ``max_replicas``
(``min_replicas=0`` scales it to zero when idle). An application is addressed by
its name, which is unique in the install.

Applications need a Kubernetes install. A Compose install answers ``not_configured``,
which the client raises as ``ApplicationsNotConfiguredError``.

Mixed into ``KiongaClient``. This module holds what both clients share: response
models, errors and the request validation. The gateway is the authority on
validation; the checks here catch mistakes before a round trip.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .errors import ConflictError, NotConfiguredError, ServerError
from .models import SCALING_METRICS, Scaling, scaling_body
from .sse import ServerSentEvent, StreamError

__all__ = [
    "Application",
    "ApplicationCondition",
    "ApplicationEvent",
    "ApplicationFailed",
    "ApplicationLogLine",
    "ApplicationPodLogs",
    "ApplicationStatus",
    "ApplicationTimeout",
    "ApplicationsNotConfiguredError",
    "GitSource",
    "SCALING_METRICS",
    "Scaling",
]

# A name becomes the first label of the application's host name, so it must be a
# DNS label: lower case letters, digits and hyphens, starting with a letter.
NAME = re.compile(r"^[a-z]([-a-z0-9]{0,61}[a-z0-9])?$")
ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
VISIBILITIES = ("public", "sso")
MAX_REPLICAS = 100
MAX_LOG_TAIL = 2000
MAX_FOLLOW_SECONDS = 3600
# The gateway ends a followed stream with this reason when its time slice is used up;
# any other end reason means the pods' logs are finished.
SLICE_END = "max_seconds"
# The fields a create or a replace accepts (the gateway rejects unknown fields).
REQUEST_FIELDS = ("name", "project_id", "image", "source", "port", "env", "secret_refs", "resources",
                  "replicas", "scaling", "health_path", "visibility")
# Phases the gateway reports. "missing" means the Kubernetes resource is gone.
READY_PHASES = frozenset({"ready", "running", "scaled-to-zero"})
FAILED_PHASES = frozenset({"failed", "missing"})

ENABLE_HINT = "Applications run only on a Kubernetes install of Kionga."


class _Open(BaseModel):
    # Keep fields the SDK doesn't model yet, so nothing the gateway sends is lost.
    model_config = ConfigDict(extra="allow")


class GitSource(_Open):
    """Build the application from a Git repository instead of a prebuilt image.
    The gateway defaults ``ref`` to ``main`` and ``dockerfile`` to ``Dockerfile``."""

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
    name: str
    id: str = ""
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
        # The gateway sends the phase as "status" and the rest of the status
        # (url, ready_replicas, message, conditions, last_build) beside it. A
        # nested status object is accepted too.
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
        for key in ("env", "resources", "replicas", "secret_refs"):
            if value.get(key) is None:
                value.pop(key, None)
        if not value.get("id") and value.get("name"):
            value["id"] = value["name"]  # the gateway addresses applications by name
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
        """True when the application serves requests. A scaled-to-zero application is
        ready with no running replicas: the first request starts one."""
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

    def request(self) -> dict[str, Any]:
        """This application's configuration as a create or replace request body."""
        body = self.model_dump(include=set(REQUEST_FIELDS), exclude_none=True)
        if self.scaling is not None and not self.scaling.metric:
            body.pop("scaling", None)  # let the gateway apply its default
        if self.source is not None:
            body["source"] = self.source.model_dump(include={"git", "ref", "context_dir", "dockerfile"},
                                                    exclude_defaults=True)
        # Drop what was never set, so the gateway's defaults apply to it.
        return {key: value for key, value in body.items() if value not in ("", None, 0, {}, [])}


class ApplicationEvent(_Open):
    """A Kubernetes event about the application, its pods or replica sets."""

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
            for key in ("last_seen", "last_timestamp", "ts", "first_seen"):
                if value.get(key):
                    return {**value, "timestamp": value[key]}
        return value


class ApplicationPodLogs(_Open):
    """Recent logs of one pod: ``lines`` is the log text, ``error`` why it could
    not be read in full (for example ``output truncated``)."""

    pod: str
    container: str = ""
    phase: str = ""
    lines: str = ""
    error: str = ""

    @property
    def messages(self) -> list[str]:
        return self.lines.splitlines()


class ApplicationLogLine(_Open):
    """One line of a followed log stream. ``time`` is the RFC 3339 timestamp with
    nanoseconds the kubelet stamped on it (``""`` if it had none)."""

    pod: str = ""
    container: str = ""
    time: str = ""
    line: str = ""

    @property
    def timestamp(self) -> datetime | None:
        """``time`` as a datetime, at microsecond precision."""
        parts = _STAMP.match(self.time)
        if not parts:
            return None
        micro = (parts[2] or "").ljust(6, "0")[:6]
        zone = "+00:00" if parts[3] == "Z" else parts[3]
        try:
            return datetime.fromisoformat(f"{parts[1]}.{micro}{zone}")
        except ValueError:
            return None


_STAMP = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d{1,9}))?(Z|[+-]\d\d:\d\d)$")


def instant(stamp: str) -> tuple[int, int] | None:
    """An RFC 3339 timestamp as exact ``(epoch seconds, nanoseconds)``, or ``None``."""
    parts = _STAMP.match(stamp)
    if not parts:
        return None
    try:
        whole = datetime.fromisoformat(parts[1] + ("+00:00" if parts[3] == "Z" else parts[3]))
    except ValueError:
        return None
    return int(whole.timestamp()), int((parts[2] or "0").ljust(9, "0"))


def _before(at: tuple[int, int]) -> str:
    """The instant one nanosecond before ``at``, in the gateway's own format."""
    seconds, nanos = divmod(at[0] * 1_000_000_000 + at[1] - 1, 1_000_000_000)
    text = datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    fraction = f".{nanos:09d}".rstrip("0") if nanos else ""
    return f"{text}{fraction}Z"


def rewind(stamp: str) -> str:
    """The instant one nanosecond before ``stamp``, in the gateway's own format.

    The gateway skips lines at or before the resume point. Resuming one
    nanosecond early makes it send the lines that share the last instant again,
    so ``LogCursor`` can tell the ones already delivered from the ones that were
    cut off. A stamp that can't be parsed is returned unchanged.
    """
    at = instant(stamp)
    if at is None or at[0] * 1_000_000_000 + at[1] < 1:
        return stamp
    return _before(at)


# A reconnect resumes this far before the newest instant delivered, so a line a slower
# pod had not yet sent when the connection broke is still recovered. The overlap is
# resent and removed again by ``LogCursor``.
RESUME_OVERLAP_NS = 5_000_000_000


class LogCursor:
    """Decides which lines of a followed stream are new, across reconnects.

    A line is identified by its instant, pod, container and text. The gateway
    resumes from a single instant, but pods stream concurrently, so a reconnect
    resumes ``RESUME_OVERLAP_NS`` before the newest instant delivered (``resume``):
    a line a slower pod had not yet sent is recovered, and the gateway resends the
    lines already delivered in that window. The cursor counts, per instant, pod,
    container and text, how many it delivered in the window and skips that many of the
    resend wherever in the new connection they arrive. Identical lines, from one pod
    or several, are delivered as many times as they occurred: nothing is deduplicated
    by text alone. State is bounded by the lines in the window. Call ``connected``
    whenever a connection opens.

    Not recoverable: a line the gateway sent later than the window after a newer
    line, and a line with no instant (the gateway's own "log stream unavailable"
    notice), which can't be placed and is delivered each time it is sent.
    """

    def __init__(self) -> None:
        self.newest: int | None = None  # nanoseconds since the epoch
        self._delivered: Counter[tuple[int, str, str, str]] = Counter()
        self._carry: Counter[tuple[int, str, str, str]] = Counter()
        self._replayed: Counter[tuple[int, str, str, str]] = Counter()
        self._floor: int | None = None
        self._limit = 1024

    def _window_start(self) -> int | None:
        return None if self.newest is None else max(self.newest - RESUME_OVERLAP_NS, 0)

    def _prune(self) -> None:
        start = self._window_start()
        self._delivered = Counter({k: n for k, n in self._delivered.items() if start is None or k[0] >= start})
        self._limit = max(1024, 2 * len(self._delivered))

    def connected(self) -> None:
        self._prune()
        self._floor = self._window_start()
        self._carry = Counter(self._delivered)
        self._replayed = Counter()

    def resume(self, last_id: str) -> str:
        """The ``Last-Event-ID`` to reconnect with: just before the start of the window."""
        start = self._window_start()
        if start is None or start < 1:
            return last_id
        return _before((start // 1_000_000_000, start % 1_000_000_000))

    def accept(self, line: ApplicationLogLine) -> bool:
        parsed = instant(line.time) if line.time else None
        if parsed is None:
            return True  # no instant to order or resume by
        at = parsed[0] * 1_000_000_000 + parsed[1]
        key = (at, line.pod, line.container, line.line)
        if self._floor is not None and at >= self._floor:
            self._replayed[key] += 1
            if self._replayed[key] <= self._carry[key]:
                return False  # the gateway resent a line already delivered
        if self.newest is None or at > self.newest:
            self.newest = at
        if at >= (self._window_start() or 0):
            self._delivered[key] += 1
            if len(self._delivered) > self._limit:
                self._prune()
        return True


def log_line(event: ServerSentEvent) -> ApplicationLogLine:
    try:
        return ApplicationLogLine.model_validate(json.loads(event.data))
    except ValueError as error:  # JSON or model validation
        raise StreamError(f"malformed log event from the gateway: {error}") from error


def end_reason(event: ServerSentEvent) -> str:
    try:
        reason = json.loads(event.data).get("reason", "")
    except (ValueError, AttributeError):
        return ""
    return reason if isinstance(reason, str) else ""


def log_stream_params(*, tail: int | None, container: str, since: str | datetime | None,
                      max_seconds: int | None) -> dict[str, str]:
    """Validate the arguments of a followed log stream and build its query."""
    params = {"follow": "true"}
    if tail is not None:
        if not 1 <= tail <= MAX_LOG_TAIL:
            raise ValueError(f"tail must be 1 to {MAX_LOG_TAIL}")
        params["tail"] = str(tail)
    if container:
        params["container"] = container
    if since is not None and since != "":
        if isinstance(since, datetime):
            if since.tzinfo is None:
                raise ValueError("since must carry a time zone")
            since = since.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        if instant(since) is None:
            raise ValueError("since must be an RFC 3339 timestamp, such as 2026-11-24T10:00:00Z")
        params["since"] = since
    if max_seconds is not None:
        if not 1 <= max_seconds <= MAX_FOLLOW_SECONDS:
            raise ValueError(f"max_seconds must be 1 to {MAX_FOLLOW_SECONDS}")
        params["max_seconds"] = str(max_seconds)
    return params


class ApplicationsNotConfiguredError(NotConfiguredError):
    """This install can't run applications: they need Kionga on Kubernetes.

    ``message`` says what is missing and ``steps`` how to enable applications.
    Like ``NotConfiguredError``, the error raised is also the class for its status
    (``ConflictError`` for the gateway's 409). Build one with ``from_error``.
    """

    def __init__(self, response: Any) -> None:
        super().__init__(response)
        text = ENABLE_HINT
        if self.message:
            text += f" {self.message}"
        if self.steps:
            text += " To enable them: " + " ".join(f"({n}) {step}" for n, step in enumerate(self.steps, 1))
        self.args = (text,)

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
    """Pick the most specific reason the status (or, failing that, the latest
    warning event) gives."""
    status = application.status
    if status.message:
        return status.message
    for condition in status.conditions:
        if condition.status == "False" and (condition.message or condition.reason):
            return ": ".join(part for part in (condition.reason, condition.message) if part)
    build = status.last_build or {}
    if str(build.get("status", build.get("phase", ""))).lower() == "failed":
        return f"build failed: {build.get('message') or build.get('error') or 'see build logs'}"
    warnings = [event for event in events or [] if event.type == "Warning"]
    if warnings:
        latest = max(warnings, key=lambda event: _utc(event.timestamp))
        return ": ".join(part for part in (latest.reason, latest.message) if part)
    return ""


def _utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


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
) -> dict[str, Any]:
    """Validate a full application configuration and build the request body.

    Needs a project, a name and exactly one of ``image`` or ``source``. Fields
    left out take the gateway's defaults. Raises ``ValueError`` before anything
    is sent.
    """
    if not project_id:
        raise ValueError("project_id is required")
    if not name or not NAME.match(name):
        raise ValueError(
            f"name {name!r} must be a DNS label: lower case letters, digits and hyphens, "
            "starting with a letter, at most 63 characters")
    if bool(image) == (source is not None):
        raise ValueError("give exactly one of image or source")
    body: dict[str, Any] = {"project_id": project_id, "name": name}
    if image:
        body["image"] = image
    if source is not None:
        git = source if isinstance(source, GitSource) else GitSource.model_validate(source)
        if not git.git:
            raise ValueError("source needs a git repository URL")
        body["source"] = git.model_dump(include={"git", "ref", "context_dir", "dockerfile"},
                                        exclude_defaults=True)
    if port is not None:
        if not 1 <= port <= 65535:
            raise ValueError("port must be 1 to 65535")
        body["port"] = port
    if env:
        bad = sorted(key for key in env if not ENV_NAME.match(key))
        if bad:
            raise ValueError(f"invalid environment variable names: {bad}")
        reserved = sorted(key for key in env if key.startswith("KIONGA_"))
        if reserved:
            raise ValueError(f"the KIONGA_ prefix is reserved: {reserved}")
        body["env"] = {key: str(value) for key, value in env.items()}
    if secret_refs:
        body["secret_refs"] = list(secret_refs)
    resources = {key: value for key, value in (("cpu", cpu), ("memory", memory)) if value}
    if resources:
        body["resources"] = resources
    replicas = _replicas(min_replicas, max_replicas)
    if replicas:
        body["replicas"] = replicas
    body.update(scaling_body(scaling))
    if body.get("scaling", {}).get("metric") == "cpu" and replicas.get("min") == 0:
        raise ValueError("scale to zero needs the requests metric; cpu scaling keeps at least one replica")
    if health_path is not None:
        if not health_path.startswith("/"):
            raise ValueError("health_path must start with /")
        body["health_path"] = health_path
    if visibility is not None:
        if visibility not in VISIBILITIES:
            raise ValueError(f"visibility must be one of {list(VISIBILITIES)}")
        body["visibility"] = visibility
    return body


def _replicas(minimum: int | None, maximum: int | None) -> dict[str, int]:
    if minimum is None and maximum is None:
        return {}  # the gateway's default range
    if minimum is not None and minimum < 0:
        raise ValueError("min_replicas can't be negative; 0 scales to zero")
    if maximum is not None and not 1 <= maximum <= MAX_REPLICAS:
        raise ValueError(f"max_replicas must be 1 to {MAX_REPLICAS}")
    if minimum is None:
        minimum = min(1, maximum or 1)
    if maximum is None:
        maximum = max(minimum, 1)
    if maximum < minimum:
        raise ValueError("max_replicas must be at least min_replicas")
    return {"min": minimum, "max": maximum}
