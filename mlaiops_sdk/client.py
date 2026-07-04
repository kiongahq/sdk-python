from __future__ import annotations

import os

import httpx

from .models import (
    Agent,
    AgentSession,
    AgentTrace,
    AuditEvent,
    Connection,
    Model,
    PipelineRun,
    Project,
    Readiness,
    Tool,
)


class MLAIOpsClient:
    """Typed client used from notebooks, training jobs, and automation."""

    def __init__(
        self,
        base_url: str = "http://localhost:8080",
        *,
        token: str | None = None,
        actor: str | None = None,
        timeout: float = 10.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        token = token or os.getenv("MLAIOPS_TOKEN")
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        if actor:
            headers["X-MLAIOps-Actor"] = actor
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

    def get_pipeline_run(self, run_id: str) -> PipelineRun:
        return PipelineRun.model_validate(self._request("GET", f"/api/v1/pipelines/runs/{run_id}"))

    def cancel_pipeline_run(self, run_id: str) -> PipelineRun:
        return PipelineRun.model_validate(
            self._request("POST", f"/api/v1/pipelines/runs/{run_id}/cancel", json={})
        )

    def retry_pipeline_run(self, run_id: str) -> PipelineRun:
        return PipelineRun.model_validate(
            self._request("POST", f"/api/v1/pipelines/runs/{run_id}/retry", json={})
        )

    def submit_pipeline(self, project_id: str, name: str = "training-pipeline") -> PipelineRun:
        data = self._request(
            "POST",
            "/api/v1/pipelines/submit",
            json={"project_id": project_id, "name": name},
        )
        return PipelineRun.model_validate(data)

    def list_models(self) -> list[Model]:
        return [Model.model_validate(item) for item in self._page("/api/v1/models")]

    def register_model(
        self,
        project_id: str,
        name: str,
        version: str,
        artifact_uri: str,
        *,
        metrics: dict[str, float] | None = None,
    ) -> Model:
        data = self._request(
            "POST",
            "/api/v1/models",
            json={
                "project_id": project_id,
                "name": name,
                "version": version,
                "artifact_uri": artifact_uri,
                "metrics": metrics or {},
            },
        )
        return Model.model_validate(data)

    def promote_model(self, model_id: str, stage: str) -> Model:
        return Model.model_validate(
            self._request("POST", f"/api/v1/models/{model_id}/promote", json={"stage": stage})
        )

    def deploy_model(self, model_id: str, *, canary_weight: int = 0) -> Model:
        return Model.model_validate(
            self._request(
                "POST",
                f"/api/v1/models/{model_id}/deploy",
                json={"canary_weight": canary_weight},
            )
        )

    def rollback_model(self, model_id: str) -> Model:
        return Model.model_validate(
            self._request("POST", f"/api/v1/models/{model_id}/rollback", json={})
        )

    def list_agents(self) -> list[Agent]:
        return [Agent.model_validate(item) for item in self._page("/api/v1/agents")]

    def deploy_agent(
        self,
        project_id: str,
        name: str,
        version: str,
        image: str,
        graph_module: str,
        *,
        llm_backend: str = "self-hosted",
        replicas: int = 1,
        tools: list[str] | None = None,
    ) -> Agent:
        data = self._request(
            "POST",
            "/api/v1/agents",
            json={
                "project_id": project_id,
                "name": name,
                "version": version,
                "image": image,
                "graph_module": graph_module,
                "llm_backend": llm_backend,
                "replicas": replicas,
                "tools": tools or [],
            },
        )
        return Agent.model_validate(data)

    def invoke_agent(
        self,
        agent_id: str,
        message: str,
        *,
        session_id: str = "",
        user_id: str = "",
        timeout: float = 120.0,
    ) -> dict:
        """Run one agent turn through the gateway's runtime proxy."""
        response = self._client.post(
            f"/api/v1/agents/{agent_id}/invoke",
            json={"message": message, "session_id": session_id, "user_id": user_id},
            timeout=timeout,
        )
        response.raise_for_status()
        return response.json()

    def set_agent_traffic(self, agent_id: str, canary_weight: int) -> Agent:
        return Agent.model_validate(
            self._request(
                "PUT",
                f"/api/v1/agents/{agent_id}/traffic",
                json={"canary_weight": canary_weight},
            )
        )

    def agent_sessions(self, agent_id: str) -> list[AgentSession]:
        return [
            AgentSession.model_validate(item)
            for item in self._page(f"/api/v1/agents/{agent_id}/sessions")
        ]

    def agent_traces(self, agent_id: str) -> list[AgentTrace]:
        return [
            AgentTrace.model_validate(item)
            for item in self._page(f"/api/v1/agents/{agent_id}/traces")
        ]

    def list_tools(self) -> list[Tool]:
        return [Tool.model_validate(item) for item in self._page("/api/v1/tools")]

    def register_tool(
        self,
        name: str,
        version: str,
        description: str,
        input_schema: dict,
        *,
        tags: list[str] | None = None,
    ) -> Tool:
        data = self._request(
            "POST",
            "/api/v1/tools",
            json={
                "name": name,
                "version": version,
                "description": description,
                "input_schema": input_schema,
                "tags": tags or [],
            },
        )
        return Tool.model_validate(data)

    def list_connections(self) -> list[Connection]:
        return [Connection.model_validate(item) for item in self._page("/api/v1/connections")]

    def create_connection(
        self, name: str, type: str, secret_ref: str, *, endpoint: str = ""
    ) -> Connection:
        data = self._request(
            "POST",
            "/api/v1/connections",
            json={"name": name, "type": type, "secret_ref": secret_ref, "endpoint": endpoint},
        )
        return Connection.model_validate(data)

    def test_connection(self, connection_id: str) -> Connection:
        return Connection.model_validate(
            self._request("POST", f"/api/v1/connections/{connection_id}/test", json={})
        )

    def readiness(self) -> Readiness:
        return Readiness.model_validate(self._request("GET", "/api/v1/onboarding/readiness"))

    def audit_events(self) -> list[AuditEvent]:
        return [AuditEvent.model_validate(item) for item in self._page("/api/v1/audit")]

    def _page(self, path: str) -> list[dict]:
        return self._request("GET", path)["items"]

    def _request(self, method: str, path: str, **kwargs: object):
        response = self._client.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json()
