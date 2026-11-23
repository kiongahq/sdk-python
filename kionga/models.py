from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class GitRepository(BaseModel):
    url: str
    provider: str
    default_branch: str
    last_commit: str | None = None
    synced_at: datetime | None = None


class Project(BaseModel):
    id: str
    name: str
    description: str
    template: str
    template_version: str = ""
    framework: str = ""
    accelerator: str = ""
    requested_profile: str = ""
    capabilities: list[str] = Field(default_factory=list)
    scaffold_command: str = ""
    namespace: str
    status: str
    created_at: datetime
    owner_subject: str | None = None
    repository: GitRepository | None = None

    @field_validator("capabilities", mode="before")
    @classmethod
    def empty_capabilities(cls, value: object) -> object:
        # Older projects and the Go encoder can represent an empty slice as null.
        return [] if value is None else value


class ProjectTemplate(BaseModel):
    id: str
    version: str
    name: str
    category: str
    description: str
    frameworks: list[str]
    accelerators: list[str]
    capabilities: list[str]
    required_services: list[str]
    recommended_profile: str


class PipelineRun(BaseModel):
    id: str
    project_id: str
    name: str
    status: str
    progress: int
    created_at: datetime
    updated_at: datetime
    parent_run_id: str | None = None
    engine_run_id: str | None = None
    definition_id: str | None = None
    execution_mode: str | None = None
    parameters: dict = Field(default_factory=dict)
    steps: list[dict] = Field(default_factory=list)
    logs: list[dict] = Field(default_factory=list)

    @field_validator("parameters", "steps", "logs", mode="before")
    @classmethod
    def empty_run_collections(cls, value: object, info) -> object:
        if value is None:
            return {} if info.field_name == "parameters" else []
        return value


class PipelineDefinition(BaseModel):
    id: str
    project_id: str
    name: str
    version: str
    execution_mode: str
    jobs: list[dict]
    repository_url: str | None = None
    commit_sha: str | None = None
    created_at: datetime
    updated_at: datetime


SCALING_METRICS = ("requests", "cpu")
# The gateway's limits: concurrent requests per replica, or CPU utilisation percent.
SCALING_TARGETS = {"requests": (1, 10000), "cpu": (1, 100)}


class Scaling(BaseModel):
    """What drives autoscaling: ``requests`` (concurrent requests per replica, which
    allows scale to zero) or ``cpu`` (utilisation percent of the CPU request).

    A response may carry an empty metric (an agent deployed before scaling
    existed); ``scaling_body`` validates what you send.
    """

    model_config = ConfigDict(extra="allow")

    metric: str = ""
    target: int = 0


def scaling_body(scaling: Scaling | dict | None) -> dict:
    """``{"scaling": {...}}`` for a request, or ``{}``. Raises ``ValueError``.

    A target left out (or 0) takes the gateway's default for the metric: 100
    concurrent requests per replica, or 80% CPU.
    """
    if scaling is None:
        return {}
    rule = scaling if isinstance(scaling, Scaling) else Scaling.model_validate(scaling)
    if rule.metric not in SCALING_TARGETS:
        raise ValueError(f"scaling metric must be one of {list(SCALING_METRICS)}")
    if rule.target == 0:
        return {"scaling": {"metric": rule.metric}}
    low, high = SCALING_TARGETS[rule.metric]
    if not low <= rule.target <= high:
        raise ValueError(f"scaling target for {rule.metric} must be {low} to {high}")
    return {"scaling": {"metric": rule.metric, "target": rule.target}}


# Functions scale to zero by default on Kubernetes: the gateway's range is 0 to 5.
FUNCTION_REPLICAS = (0, 5)
MAX_WORKLOAD_REPLICAS = 100


def function_scaling_body(min_replicas: int | None, max_replicas: int | None,
                          scaling: Scaling | dict | None) -> dict:
    """``replicas`` and ``scaling`` for a function deploy, or ``{}``. A bound left
    out takes the gateway's default. Raises ``ValueError``."""
    body = scaling_body(scaling)
    if min_replicas is not None or max_replicas is not None:
        low = FUNCTION_REPLICAS[0] if min_replicas is None else min_replicas
        high = max(low, FUNCTION_REPLICAS[1]) if max_replicas is None else max_replicas
        if low < 0:
            raise ValueError("min_replicas can't be negative; 0 scales to zero")
        if not 1 <= high <= MAX_WORKLOAD_REPLICAS:
            raise ValueError(f"max_replicas must be 1 to {MAX_WORKLOAD_REPLICAS}")
        if high < low:
            raise ValueError("max_replicas must be at least min_replicas")
        body["replicas"] = {"min": low, "max": high}
    if body.get("scaling", {}).get("metric") == "cpu" and body.get("replicas", {}).get("min", 0) == 0:
        raise ValueError("cpu scaling keeps at least one replica: set min_replicas to 1 or more")
    return body


class Function(BaseModel):
    name: str
    project_id: str = ""
    image: str
    status: str
    replicas: int = 0
    env_vars: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)
    cpu: str | None = None
    memory: str | None = None
    replica_range: dict[str, int] | None = None
    scaling: Scaling | None = None
    message: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None


# Deployment statuses. "serving" means the endpoint answers; "failed" carries a
# reason; "stopped", "retired" and "rolled_back" mean this version is no longer
# being deployed. "pending" and the older "deploying" are in progress.
MODEL_SERVING = frozenset({"serving"})
MODEL_DEPLOY_FAILED = frozenset({"failed", "stopped", "retired", "rolled_back"})


class Model(BaseModel):
    # Keep fields the SDK doesn't model yet, such as a deployment reason.
    model_config = ConfigDict(extra="allow")

    source: dict[str, str] | None = None
    id: str
    project_id: str
    name: str
    version: str
    stage: str
    artifact_uri: str
    serving_image: str | None = None
    metrics: dict[str, float]
    created_at: datetime
    gate_status: str = ""
    deployment_status: str = ""
    canary_weight: int = 0
    endpoint_url: str | None = None
    previous_stage: str | None = None
    deployment_message: str = ""

    @property
    def serving(self) -> bool:
        """True when the deployment answers predictions at ``endpoint_url``."""
        return self.deployment_status in MODEL_SERVING

    @property
    def deployment_failed(self) -> bool:
        return self.deployment_status in MODEL_DEPLOY_FAILED

    @property
    def deployment_reason(self) -> str:
        """Why the deployment failed or is waiting, when the gateway says."""
        if self.deployment_message:
            return self.deployment_message
        extra = self.model_extra or {}
        return next((str(extra[key]) for key in ("deployment_error", "message", "error") if extra.get(key)), "")


class AgentAutoscaling(BaseModel):
    min_replicas: int = 1
    max_replicas: int = 1


class AgentResources(BaseModel):
    cpu: str = "500m"
    memory: str = "1Gi"
    gpu: int = 0
    gpu_type: str | None = None


class Agent(BaseModel):
    id: str
    project_id: str
    owner_subject: str | None = None
    name: str
    version: str
    image: str
    graph_module: str
    llm_backend: str
    status: str
    endpoint_url: str | None = None
    replicas: int
    autoscaling: AgentAutoscaling = Field(default_factory=AgentAutoscaling)
    scaling: Scaling | None = None
    resources: AgentResources = Field(default_factory=AgentResources)
    canary_weight: int
    tools: list[str]
    created_at: datetime


class Tool(BaseModel):
    id: str
    name: str
    version: str
    description: str
    tags: list[str]
    input_schema: dict
    status: str
    created_at: datetime


class Connection(BaseModel):
    id: str
    name: str
    type: str
    endpoint: str
    secret_ref: str
    status: str
    created_at: datetime
    checked_at: datetime | None = None
    message: str | None = None


class ReadinessItem(BaseModel):
    key: str
    label: str
    status: str
    description: str
    action: str | None = None


class Readiness(BaseModel):
    percent: int
    ready: bool
    items: list[ReadinessItem]


class AgentSession(BaseModel):
    id: str
    agent_id: str
    user_id: str
    status: str
    current_node: str
    turns: int
    input_tokens: int
    output_tokens: int
    cost_usd: float
    started_at: datetime
    updated_at: datetime


class AgentTrace(BaseModel):
    id: str
    agent_id: str
    session_id: str
    name: str
    status: str
    duration_ms: int
    tokens: int
    metadata: dict | None = None
    created_at: datetime


class AuditEvent(BaseModel):
    id: str
    action: str
    resource: str
    resource_id: str
    actor: str
    metadata: dict | None = None
    created_at: datetime


class ModelDeployment(BaseModel):
    """The live deployment of one model version (``GET /models/{id}/deployment``)."""

    model_config = ConfigDict(extra="allow", protected_namespaces=())

    model_id: str
    name: str = ""
    version: str = ""
    status: str = ""
    endpoint_url: str | None = None
    message: str = ""
    canary_weight: int = 0

    @classmethod
    def from_model(cls, model: Model) -> ModelDeployment:
        return cls(model_id=model.id, name=model.name, version=model.version,
                   status=model.deployment_status, endpoint_url=model.endpoint_url,
                   message=model.deployment_reason, canary_weight=model.canary_weight)

    @property
    def serving(self) -> bool:
        return self.status in MODEL_SERVING

    @property
    def failed(self) -> bool:
        return self.status in MODEL_DEPLOY_FAILED


class ModelDeploymentFailed(Exception):
    """The deployment failed or was stopped. ``deployment`` is its last state."""

    def __init__(self, deployment: ModelDeployment) -> None:
        reason = deployment.message or deployment.status
        super().__init__(f"model {deployment.name} {deployment.version} did not deploy: {reason}")
        self.deployment = deployment
        self.reason = deployment.message


class ModelDeploymentTimeout(TimeoutError):
    """The model was not serving when ``wait_for_model_deployment`` gave up."""

    def __init__(self, deployment: ModelDeployment, timeout: float) -> None:
        text = (f"model {deployment.name} {deployment.version} still "
                f"{deployment.status or 'pending'} after {timeout:g}s")
        super().__init__(f"{text}: {deployment.message}" if deployment.message else text)
        self.deployment = deployment
        self.reason = deployment.message
