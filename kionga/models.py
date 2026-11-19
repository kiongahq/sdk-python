from datetime import datetime

from typing import Literal

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
    created_at: datetime | None = None
    updated_at: datetime | None = None


class Scaling(BaseModel):
    """What drives autoscaling: ``requests`` (in-flight requests per replica, which
    allows scale to zero) or ``cpu`` (percent of the CPU request)."""

    model_config = ConfigDict(extra="allow")

    metric: Literal["requests", "cpu"]
    target: int = Field(gt=0)


# Deployment statuses. "serving" and "failed" are final for a deploy; the
# others mean the deploy is still in progress. Older gateways report
# "deploying" before "serving".
MODEL_SERVING = frozenset({"serving"})
MODEL_DEPLOY_FAILED = frozenset({"failed"})


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


class ModelDeploymentFailed(Exception):
    """The model deployment failed. ``model`` is its last state and ``reason`` why."""

    def __init__(self, model: Model, reason: str = "") -> None:
        super().__init__(f"model {model.name} {model.version} failed to deploy: {reason or 'no reason given'}")
        self.model = model
        self.reason = reason


class ModelDeploymentTimeout(TimeoutError):
    """The model was not serving when ``wait_for_model_deployment`` gave up."""

    def __init__(self, model: Model, timeout: float) -> None:
        reason = model.deployment_reason
        text = f"model {model.name} {model.version} still {model.deployment_status or 'pending'} after {timeout:g}s"
        super().__init__(f"{text}: {reason}" if reason else text)
        self.model = model
        self.reason = reason
