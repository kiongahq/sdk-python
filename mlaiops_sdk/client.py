from __future__ import annotations

import httpx

from .models import PipelineRun, Project


class MLAIOpsClient:
    """Typed client used from notebooks, training jobs, and automation."""

    def __init__(
        self,
        base_url: str = "http://localhost:8080",
        *,
        token: str | None = None,
        timeout: float = 10.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers=headers,
            timeout=timeout,
            transport=transport,
        )

    def __enter__(self) -> MLAIOpsClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def health(self) -> dict[str, str]:
        return self._request("GET", "/api/v1/health")

    def list_projects(self) -> list[Project]:
        return [Project.model_validate(item) for item in self._request("GET", "/api/v1/projects")]

    def create_project(
        self,
        name: str,
        *,
        description: str = "",
        template: str = "tabular-classification",
    ) -> Project:
        data = self._request(
            "POST",
            "/api/v1/projects",
            json={"name": name, "description": description, "template": template},
        )
        return Project.model_validate(data)

    def list_pipeline_runs(self) -> list[PipelineRun]:
        return [
            PipelineRun.model_validate(item)
            for item in self._request("GET", "/api/v1/pipelines/runs")
        ]

    def submit_pipeline(self, project_id: str, name: str = "training-pipeline") -> PipelineRun:
        data = self._request(
            "POST",
            "/api/v1/pipelines/submit",
            json={"project_id": project_id, "name": name},
        )
        return PipelineRun.model_validate(data)

    def _request(self, method: str, path: str, **kwargs: object):
        response = self._client.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json()
