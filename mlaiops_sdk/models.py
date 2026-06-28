from datetime import datetime

from pydantic import BaseModel, Field


class Project(BaseModel):
    id: str
    name: str
    description: str
    template: str
    namespace: str
    status: str
    created_at: datetime


class PipelineRun(BaseModel):
    id: str
    project_id: str
    name: str
    status: str
    progress: int
    created_at: datetime
    updated_at: datetime
    parent_run_id: str | None = None
    steps: list[dict] = Field(default_factory=list)
    logs: list[dict] = Field(default_factory=list)


class Model(BaseModel):
    id: str
    project_id: str
    name: str
    version: str
    stage: str
    artifact_uri: str
    metrics: dict[str, float]
    created_at: datetime
    gate_status: str = ""
    deployment_status: str = ""
    canary_weight: int = 0
    endpoint_url: str | None = None
    previous_stage: str | None = None


class Agent(BaseModel):
    id: str
    project_id: str
    name: str
    version: str
    image: str
    graph_module: str
    llm_backend: str
    status: str
    replicas: int
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
