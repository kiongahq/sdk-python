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
