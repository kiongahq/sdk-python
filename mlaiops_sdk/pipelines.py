from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class ResourceRequirements:
    cpu: str = "500m"
    memory: str = "1Gi"
    gpu: int = 0


@dataclass(frozen=True)
class PipelineStep:
    name: str
    image: str
    command: list[str]
    depends_on: list[str] = field(default_factory=list)
    environment: dict[str, str] = field(default_factory=dict)
    resources: ResourceRequirements = field(default_factory=ResourceRequirements)
    retries: int = 1


@dataclass
class Pipeline:
    name: str
    steps: list[PipelineStep] = field(default_factory=list)

    def add(self, step: PipelineStep) -> Pipeline:
        if any(existing.name == step.name for existing in self.steps):
            raise ValueError(f"duplicate step: {step.name}")
        known = {existing.name for existing in self.steps}
        missing = set(step.depends_on) - known
        if missing:
            raise ValueError(f"unknown dependencies: {', '.join(sorted(missing))}")
        self.steps.append(step)
        return self

    def compile(self) -> dict[str, Any]:
        if not self.steps:
            raise ValueError("pipeline must contain at least one step")
        return {
            "apiVersion": "mlaiops.io/v1alpha1",
            "kind": "NexusPipeline",
            "metadata": {"name": self.name},
            "spec": {"steps": [asdict(step) for step in self.steps]},
        }
