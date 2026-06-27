from datetime import datetime

from pydantic import BaseModel


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


class Model(BaseModel):
    id: str
    project_id: str
    name: str
    version: str
    stage: str
    artifact_uri: str
    metrics: dict[str, float]
    created_at: datetime


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


class AuditEvent(BaseModel):
    id: str
    action: str
    resource: str
    resource_id: str
    actor: str
    metadata: dict | None = None
    created_at: datetime
