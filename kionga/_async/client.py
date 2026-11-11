# Async source of truth. kionga/_sync is generated from this by scripts/unasync.py.
from __future__ import annotations

import asyncio
import os
import time
from urllib.parse import quote

import httpx

from ..models import (
    Agent,
    AgentSession,
    AgentTrace,
    AuditEvent,
    Connection,
    Function,
    Model,
    PipelineRun,
    PipelineDefinition,
    Project,
    ProjectTemplate,
    Readiness,
    Tool,
)
from ..errors import KiongaConnectionError, KiongaTimeoutError, error_for_response
from ..retry import RetryPolicy
from .data import DataAPI
from .sse import StreamingAPI
from .teams import TeamsAPI
from ..transport import require_token_transport

__all__ = ["AsyncKiongaClient"]


class AsyncKiongaClient(TeamsAPI, DataAPI, StreamingAPI):
    """Typed client used from notebooks, training jobs, and automation."""

    def __init__(
        self,
        base_url: str | None = None,
        *,
        token: str | None = None,
        actor: str | None = None,
        timeout: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
        allow_insecure_http: bool | None = None,
        retry: RetryPolicy | None = None,
    ) -> None:
        self.retry = retry or RetryPolicy()
        self._sleep = asyncio.sleep
        if base_url is None:
            base_url = os.getenv("KIONGA_URL") or "http://localhost:8080"
        token = token or os.getenv("KIONGA_TOKEN")
        if token:
            # Plaintext HTTP only to loopback or an in-cluster service name;
            # otherwise opt in with allow_insecure_http / KIONGA_ALLOW_INSECURE_HTTP.
            require_token_transport(base_url, allow_insecure_http=allow_insecure_http)
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        if actor:
            headers["X-Kionga-Actor"] = actor
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers=headers,
            timeout=timeout,
            transport=transport,
        )

    async def __aenter__(self) -> AsyncKiongaClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def health(self) -> dict[str, str]:
        return await self._request("GET", "/api/v1/health")

    async def list_projects(self) -> list[Project]:
        return [Project.model_validate(item) for item in await self._request("GET", "/api/v1/projects")]

    async def list_project_templates(self) -> list[ProjectTemplate]:
        return [
            ProjectTemplate.model_validate(item)
            for item in await self._page("/api/v1/project-templates")
        ]

    async def get_project_template(
        self, template_id: str, *, version: str = ""
    ) -> ProjectTemplate:
        suffix = f"?version={version}" if version else ""
        return ProjectTemplate.model_validate(
            await self._request("GET", f"/api/v1/project-templates/{template_id}{suffix}")
        )

    async def create_project(
        self,
        name: str,
        *,
        description: str = "",
        template: str = "production-ml",
        template_version: str = "",
        framework: str = "",
        accelerator: str = "",
        requested_profile: str = "",
        repository_url: str = "",
        default_branch: str = "main",
    ) -> Project:
        data = await self._request(
            "POST",
            "/api/v1/projects",
            json={
                "name": name,
                "description": description,
                "template": template,
                "template_version": template_version,
                "framework": framework,
                "accelerator": accelerator,
                "requested_profile": requested_profile,
                "repository_url": repository_url,
                "default_branch": default_branch,
            },
        )
        return Project.model_validate(data)

    async def get_project(self, project_id: str) -> Project:
        return Project.model_validate(await self._request("GET", f"/api/v1/projects/{project_id}"))

    async def connect_repository(self, project_id: str, url: str, *, default_branch: str = "main") -> Project:
        return Project.model_validate(await self._request("PUT", f"/api/v1/projects/{project_id}/repository", json={"url": url, "default_branch": default_branch}))

    async def list_pipeline_runs(self) -> list[PipelineRun]:
        return [
            PipelineRun.model_validate(item)
            for item in await self._request("GET", "/api/v1/pipelines/runs")
        ]

    async def get_pipeline_run(self, run_id: str) -> PipelineRun:
        return PipelineRun.model_validate(await self._request("GET", f"/api/v1/pipelines/runs/{run_id}"))

    async def cancel_pipeline_run(self, run_id: str) -> PipelineRun:
        return PipelineRun.model_validate(
            await self._request("POST", f"/api/v1/pipelines/runs/{run_id}/cancel", json={})
        )

    async def retry_pipeline_run(self, run_id: str) -> PipelineRun:
        return PipelineRun.model_validate(
            await self._request("POST", f"/api/v1/pipelines/runs/{run_id}/retry", json={})
        )

    async def submit_pipeline(self, project_id: str, name: str = "training-pipeline", *, definition_id: str = "", parameters: dict | None = None) -> PipelineRun:
        data = await self._request(
            "POST",
            "/api/v1/pipelines/submit",
            json={"project_id": project_id, "name": name, "definition_id": definition_id, "parameters": parameters or {}},
        )
        return PipelineRun.model_validate(data)

    async def list_pipeline_definitions(self) -> list[PipelineDefinition]:
        return [PipelineDefinition.model_validate(item) for item in await self._page("/api/v1/pipelines/definitions")]

    async def create_pipeline_definition(self, project_id: str, name: str, version: str, jobs: list[dict], *, execution_mode: str = "prefect", repository_url: str = "", commit_sha: str = "") -> PipelineDefinition:
        data = await self._request("POST", "/api/v1/pipelines/definitions", json={"project_id": project_id, "name": name, "version": version, "execution_mode": execution_mode, "jobs": jobs, "repository_url": repository_url, "commit_sha": commit_sha})
        return PipelineDefinition.model_validate(data)

    async def list_functions(self) -> list[Function]:
        return [Function.model_validate(item) for item in await self._page("/api/v1/functions")]

    async def deploy_function(self, project_id: str, name: str, image: str, *, cpu: str = "500m", memory: str = "512Mi", env_vars: dict[str, str] | None = None, annotations: dict[str, str] | None = None) -> Function:
        data = await self._request("POST", "/api/v1/functions", json={"project_id": project_id, "name": name, "image": image, "cpu": cpu, "memory": memory, "env_vars": env_vars or {}, "annotations": annotations or {}})
        return Function.model_validate(data)

    async def invoke_function(self, name: str, payload: dict) -> dict:
        return await self._request("POST", f"/api/v1/functions/{name}/invoke", json=payload)

    async def invoke_function_async(self, name: str, payload: dict) -> dict:
        return await self._request("POST", f"/api/v1/functions/{name}/invoke-async", json=payload)

    async def delete_function(self, name: str) -> None:
        await self._request("DELETE", f"/api/v1/functions/{name}")

    async def list_models(self) -> list[Model]:
        return [Model.model_validate(item) for item in await self._page("/api/v1/models")]

    async def search_huggingface_models(self, search: str = "", *, task: str = "") -> list[dict]:
        return await self._request("GET", "/api/v1/models/huggingface",
                             params={"search": search, "task": task})["items"]

    async def import_huggingface_model(
        self, project_id: str, repo_id: str, *, revision: str = "main"
    ) -> Model:
        """Register immutable provenance; weights are downloaded separately in the workspace."""
        return Model.model_validate(await self._request(
            "POST", "/api/v1/models/huggingface/import",
            json={"project_id": project_id, "repo_id": repo_id, "revision": revision},
        ))

    async def register_model(
        self,
        project_id: str,
        name: str,
        version: str,
        artifact_uri: str,
        *,
        metrics: dict[str, float] | None = None,
        serving_image: str | None = None,
    ) -> Model:
        data = await self._request(
            "POST",
            "/api/v1/models",
            json={
                "project_id": project_id,
                "name": name,
                "version": version,
                "artifact_uri": artifact_uri,
                "metrics": metrics or {},
                **({"serving_image": serving_image} if serving_image else {}),
            },
        )
        return Model.model_validate(data)

    async def promote_model(self, model_id: str, stage: str) -> Model:
        return Model.model_validate(
            await self._request("POST", f"/api/v1/models/{model_id}/promote", json={"stage": stage})
        )

    async def deploy_model(self, model_id: str, *, canary_weight: int = 0) -> Model:
        return Model.model_validate(
            await self._request(
                "POST",
                f"/api/v1/models/{model_id}/deploy",
                json={"canary_weight": canary_weight},
            )
        )

    async def rollback_model(self, model_id: str) -> Model:
        return Model.model_validate(
            await self._request("POST", f"/api/v1/models/{model_id}/rollback", json={})
        )

    async def list_agents(self) -> list[Agent]:
        return [Agent.model_validate(item) for item in await self._page("/api/v1/agents")]

    async def deploy_agent(
        self,
        project_id: str,
        name: str,
        version: str,
        image: str,
        graph_module: str,
        *,
        llm_backend: str = "mock",
        replicas: int = 1,
        max_replicas: int | None = None,
        cpu: str = "500m",
        memory: str = "1Gi",
        gpu: int = 0,
        gpu_type: str = "nvidia.com/gpu",
        tools: list[str] | None = None,
    ) -> Agent:
        data = await self._request(
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
                "autoscaling": {
                    "min_replicas": replicas,
                    "max_replicas": max_replicas or replicas,
                },
                "resources": {
                    "cpu": cpu,
                    "memory": memory,
                    "gpu": gpu,
                    "gpu_type": gpu_type if gpu else "",
                },
                "tools": tools or [],
            },
        )
        return Agent.model_validate(data)

    async def invoke_agent(
        self,
        agent_id: str,
        message: str,
        *,
        session_id: str = "",
        user_id: str = "",
        timeout: float = 120.0,
    ) -> dict:
        """Run one agent turn through the gateway's runtime proxy."""
        return await self._request(
            "POST",
            f"/api/v1/agents/{agent_id}/invoke",
            json={"message": message, "session_id": session_id, "user_id": user_id},
            timeout=timeout,
        )

    async def set_agent_traffic(self, agent_id: str, canary_weight: int) -> Agent:
        return Agent.model_validate(
            await self._request(
                "PUT",
                f"/api/v1/agents/{agent_id}/traffic",
                json={"canary_weight": canary_weight},
            )
        )

    async def agent_sessions(self, agent_id: str) -> list[AgentSession]:
        return [
            AgentSession.model_validate(item)
            for item in await self._page(f"/api/v1/agents/{agent_id}/sessions")
        ]

    async def agent_traces(self, agent_id: str) -> list[AgentTrace]:
        return [
            AgentTrace.model_validate(item)
            for item in await self._page(f"/api/v1/agents/{agent_id}/traces")
        ]

    async def agent_metrics(self, agent_id: str) -> dict:
        """Read live CPU and memory use when Kubernetes metrics are available."""
        return await self._request("GET", f"/api/v1/agents/{quote(agent_id, safe='')}/metrics")

    async def list_tools(self) -> list[Tool]:
        return [Tool.model_validate(item) for item in await self._page("/api/v1/tools")]

    async def register_tool(
        self,
        name: str,
        version: str,
        description: str,
        input_schema: dict,
        *,
        tags: list[str] | None = None,
    ) -> Tool:
        data = await self._request(
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

    async def list_connections(self) -> list[Connection]:
        return [Connection.model_validate(item) for item in await self._page("/api/v1/connections")]

    async def list_knowledge_bases(self) -> list[dict]:
        """Discover only bases visible to the caller's assigned projects."""
        return await self._page("/api/v1/knowledge-bases")

    async def list_datasets(self) -> list[dict]:
        """Discover dataset prefixes visible to the caller's project and bucket grant."""
        return await self._page("/api/v1/datasets")

    async def list_dataset_objects(self, dataset_id: str, *, prefix: str = "") -> dict:
        return await self._request(
            "GET", f"/api/v1/datasets/{quote(dataset_id, safe='')}/objects",
            params={"prefix": prefix},
        )

    async def preview_dataset_object(self, dataset_id: str, key: str) -> dict:
        return await self._request(
            "GET", f"/api/v1/datasets/{quote(dataset_id, safe='')}/object",
            params={"key": key},
        )

    async def list_registered_storage(self) -> list[dict]:
        """List S3-compatible sources assigned to the caller's projects."""
        return await self._page("/api/v1/registered-storage")

    async def list_registered_objects(self, source_id: str, *, prefix: str = "") -> dict:
        return await self._request(
            "GET", f"/api/v1/registered-storage/{quote(source_id, safe='')}/objects",
            params={"prefix": prefix},
        )

    async def preview_registered_object(self, source_id: str, key: str) -> dict:
        return await self._request(
            "GET", f"/api/v1/registered-storage/{quote(source_id, safe='')}/object",
            params={"key": key},
        )

    async def add_knowledge_chunk(
        self, base_id: str, text: str, vector: list[float], *, metadata: dict | None = None
    ) -> dict:
        return await self._request(
            "POST", f"/api/v1/knowledge-bases/{quote(base_id, safe='')}/chunks",
            json={"text": text, "vector": vector, "metadata": metadata or {}},
        )

    async def search_knowledge_base(
        self, base_id: str, vector: list[float], *, limit: int = 5
    ) -> list[dict]:
        return await self._request(
            "POST", f"/api/v1/knowledge-bases/{quote(base_id, safe='')}/search",
            json={"vector": vector, "limit": limit},
        )["items"]

    async def list_agent_api_connections(self) -> list[dict]:
        """Return approved operations without upstream URLs or credentials."""
        return await self._page("/api/v1/agent-api-connections")

    async def invoke_agent_api_connection(self, connection_id: str, payload: dict | list) -> dict:
        return await self._request(
            "POST", f"/api/v1/agent-api-connections/{quote(connection_id, safe='')}/invoke",
            json=payload,
        )

    async def list_langflow_snapshots(self) -> list[dict]:
        return await self._page("/api/v1/langflow-snapshots")

    async def get_langflow_snapshot(self, snapshot_id: str) -> dict:
        return await self._request("GET", f"/api/v1/langflow-snapshots/{quote(snapshot_id, safe='')}")

    async def list_langflow_flows(self, workspace_id: str) -> list[dict]:
        return await self._page(f"/api/v1/langflow-workspaces/{quote(workspace_id, safe='')}/flows")

    async def capture_langflow_flow(
        self, workspace_id: str, flow_id: str, project_id: str, *,
        snapshot_id: str = "", revision: int = 0,
    ) -> dict:
        """Capture a saved editor flow, checking the expected revision on updates."""
        return await self._request(
            "POST", f"/api/v1/langflow-workspaces/{quote(workspace_id, safe='')}/snapshots",
            json={"flow_id": flow_id, "project_id": project_id,
                  "snapshot_id": snapshot_id, "revision": revision},
        )

    async def publish_langflow_snapshot(
        self, snapshot_id: str, *, revision: int = 0, min_replicas: int = 1,
        max_replicas: int = 1, cpu: str = "500m", memory: str = "1Gi",
    ) -> dict:
        """Deploy a retained or current revision independently of its editor."""
        return await self._request(
            "POST", f"/api/v1/langflow-snapshots/{quote(snapshot_id, safe='')}/publish",
            json={"revision": revision,
                  "autoscaling": {"min_replicas": min_replicas, "max_replicas": max_replicas},
                  "resources": {"cpu": cpu, "memory": memory}},
        )

    async def activate_langflow_deployment(self, snapshot_id: str, agent_id: str) -> dict:
        """Point the stable snapshot route at a ready published revision."""
        return await self._request(
            "POST", f"/api/v1/langflow-snapshots/{quote(snapshot_id, safe='')}/activate",
            json={"agent_id": agent_id},
        )

    async def invoke_langflow_snapshot(
        self, snapshot_id: str, message: str, *, session_id: str = "", user_id: str = ""
    ) -> dict:
        """Invoke the active revision through the snapshot's stable route."""
        return await self._request(
            "POST", f"/api/v1/langflow-snapshots/{quote(snapshot_id, safe='')}/invoke",
            json={"message": message, "session_id": session_id, "user_id": user_id},
        )

    # --- native Agent Designer (contracts agent-graph) ---

    async def agent_designer_components(self) -> dict:
        """The component registry: node kinds, typed ports, settings, side effects."""
        return await self._request("GET", "/api/v1/agent-designs/components")

    async def agent_designer_templates(self) -> list[dict]:
        return await self._page("/api/v1/agent-designs/templates")

    async def list_agent_designs(self) -> list[dict]:
        return await self._page("/api/v1/agent-designs")

    async def get_agent_design(self, design_id: str) -> dict:
        return await self._request("GET", f"/api/v1/agent-designs/{quote(design_id, safe='')}")

    async def create_agent_design(self, project_id: str, name: str, graph: dict, *, description: str = "") -> dict:
        """Create a design from a graph ({nodes, edges, settings?}), e.g. a template's graph."""
        return await self._request("POST", "/api/v1/agent-designs", json=self._design_body(project_id, name, graph, description, 0))

    async def save_agent_design(self, design: dict, graph: dict | None = None) -> dict:
        """Save a new revision; ``design['revision']`` must be the revision you read (409 otherwise)."""
        graph = graph or design
        return await self._request(
            "PUT", f"/api/v1/agent-designs/{quote(design['id'], safe='')}",
            json=self._design_body(design["project_id"], design["name"], graph, design.get("description", ""), design["revision"]),
        )

    @staticmethod
    def _design_body(project_id: str, name: str, graph: dict, description: str, revision: int) -> dict:
        body = {"project_id": project_id, "name": name, "description": description, "revision": revision,
                "nodes": graph["nodes"], "edges": graph["edges"]}
        if graph.get("settings"):
            body["settings"] = graph["settings"]
        return body

    async def validate_agent_design(self, design_id: str) -> dict:
        """Returns {runnable, issues, content_sha256, graph} for the saved revision."""
        return await self._request("POST", f"/api/v1/agent-designs/{quote(design_id, safe='')}/validate", json={})

    async def run_agent_design(
        self, design_id: str, input: str, *, session_id: str = "", history: list[dict] | None = None,
        allow_side_effects: bool = False, stop_after: str = "", idempotency_key: str = "",
        wait: bool = True, timeout: float = 360.0, poll_interval: float = 0.5,
    ) -> dict:
        """Run the saved revision in the isolated runner. With ``wait`` the
        finished run (status, output, usage, events) is returned."""
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else {}
        run = await self._request(
            "POST", f"/api/v1/agent-designs/{quote(design_id, safe='')}/runs", headers=headers,
            json={"input": input, "session_id": session_id, "history": history or [],
                  "allow_side_effects": allow_side_effects, "stop_after": stop_after},
        )
        return await self.wait_for_agent_run(design_id, run["id"], timeout=timeout, poll_interval=poll_interval) if wait else run

    async def get_agent_run(self, design_id: str, run_id: str) -> dict:
        return await self._request("GET", f"/api/v1/agent-designs/{quote(design_id, safe='')}/runs/{quote(run_id, safe='')}")

    async def agent_run_events(self, design_id: str, run_id: str, *, after: int = -1) -> dict:
        """One polling page: {status, events, next, done}. Pass ``next`` as ``after``."""
        return await self._request(
            "GET", f"/api/v1/agent-designs/{quote(design_id, safe='')}/runs/{quote(run_id, safe='')}/events",
            params={"after": after},
        )

    async def cancel_agent_run(self, design_id: str, run_id: str) -> dict:
        return await self._request("POST", f"/api/v1/agent-designs/{quote(design_id, safe='')}/runs/{quote(run_id, safe='')}/cancel", json={})

    async def wait_for_agent_run(self, design_id: str, run_id: str, *, timeout: float = 360.0, poll_interval: float = 0.5) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            run = await self.get_agent_run(design_id, run_id)
            if run["status"] in ("succeeded", "failed", "cancelled"):
                return run
            if time.monotonic() >= deadline:
                raise TimeoutError(f"agent run {run_id} is still {run['status']}")
            await asyncio.sleep(poll_interval)

    async def get_agent_test_cases(self, design_id: str) -> dict:
        return await self._request("GET", f"/api/v1/agent-designs/{quote(design_id, safe='')}/fixtures")

    async def save_agent_test_cases(self, design_id: str, cases: list[dict], *, revision: int) -> dict:
        """Replace the design's regression cases; ``revision`` is the fixtures revision you read."""
        return await self._request("PUT", f"/api/v1/agent-designs/{quote(design_id, safe='')}/fixtures",
                             json={"revision": revision, "cases": cases})

    async def evaluate_agent_design(
        self, design_id: str, *, gate: float = 0.8, version_id: str = "", cases: list[dict] | None = None,
        allow_side_effects: bool = False, wait: bool = True, timeout: float = 1800.0, poll_interval: float = 1.0,
    ) -> dict:
        """Evaluate the saved revision (or a published version) against test cases and a pass-rate gate."""
        evaluation = await self._request(
            "POST", f"/api/v1/agent-designs/{quote(design_id, safe='')}/evaluations",
            json={"gate": gate, "version_id": version_id, "cases": cases or [], "allow_side_effects": allow_side_effects},
        )
        if not wait:
            return evaluation
        deadline = time.monotonic() + timeout
        while evaluation["status"] in ("running", "cancelling"):
            if time.monotonic() >= deadline:
                raise TimeoutError(f"evaluation {evaluation['id']} is still {evaluation['status']}")
            await asyncio.sleep(poll_interval)
            evaluation = await self.get_agent_evaluation(evaluation["id"])
        return evaluation

    async def get_agent_evaluation(self, evaluation_id: str) -> dict:
        return await self._request("GET", f"/api/v1/agent-evaluations/{quote(evaluation_id, safe='')}")

    async def list_agent_evaluations(self, design_id: str) -> list[dict]:
        return await self._page(f"/api/v1/agent-designs/{quote(design_id, safe='')}/evaluations")

    async def publish_agent_design(self, design_id: str, revision: int, *, notes: str = "", evaluation_id: str = "") -> dict:
        """Publish the saved revision as an immutable version. Unchanged behavior returns the existing version."""
        return await self._request("POST", f"/api/v1/agent-designs/{quote(design_id, safe='')}/publish",
                             json={"revision": revision, "notes": notes, "evaluation_id": evaluation_id})

    async def list_agent_versions(self, design_id: str) -> list[dict]:
        return await self._page(f"/api/v1/agent-designs/{quote(design_id, safe='')}/versions")

    async def get_agent_version(self, version_id: str) -> dict:
        return await self._request("GET", f"/api/v1/agent-versions/{quote(version_id, safe='')}")

    async def run_agent_version(self, version_id: str, input: str, *, design_id: str, session_id: str = "",
                          allow_side_effects: bool = False, wait: bool = True, timeout: float = 360.0) -> dict:
        run = await self._request("POST", f"/api/v1/agent-versions/{quote(version_id, safe='')}/runs",
                            json={"input": input, "session_id": session_id, "allow_side_effects": allow_side_effects})
        return await self.wait_for_agent_run(design_id, run["id"], timeout=timeout) if wait else run

    async def deploy_agent_version(
        self, version_id: str, *, min_replicas: int = 1, max_replicas: int = 1, cpu: str = "500m",
        memory: str = "1Gi", require_evaluation: bool = False,
    ) -> dict:
        """Deploy (or roll back to) a published version on the managed agent runtime."""
        return await self._request(
            "POST", f"/api/v1/agent-versions/{quote(version_id, safe='')}/deploy",
            json={"autoscaling": {"min_replicas": min_replicas, "max_replicas": max_replicas},
                  "resources": {"cpu": cpu, "memory": memory}, "require_evaluation": require_evaluation},
        )

    async def list_agent_approvals(self, *, status: str = "pending", project_id: str = "", run_id: str = "") -> list[dict]:
        """Approval requests from approval nodes, newest first."""
        params = {key: value for key, value in {"status": status, "project_id": project_id, "run_id": run_id}.items() if value}
        return await self._request("GET", "/api/v1/agent-approvals", params=params)["items"]

    async def decide_agent_approval(self, approval_id: str, decision: str, *, comment: str = "") -> dict:
        """Approve or reject a pending request (``decision``: approve | reject)."""
        if decision not in ("approve", "reject"):
            raise ValueError("decision must be 'approve' or 'reject'")
        return await self._request("POST", f"/api/v1/agent-approvals/{quote(approval_id, safe='')}/decision",
                             json={"decision": decision, "comment": comment})

    async def review_agent_evaluation_case(self, evaluation_id: str, case_id: str, status: str, *, note: str = "") -> dict:
        """Record a person's review (accepted | rejected | needs_changes) of one evaluated case."""
        return await self._request(
            "POST", f"/api/v1/agent-evaluations/{quote(evaluation_id, safe='')}/cases/{quote(case_id, safe='')}/review",
            json={"status": status, "note": note},
        )

    async def list_project_agent_versions(self, project_id: str = "") -> list[dict]:
        """Published versions usable by subflow, map and loop nodes."""
        return await self._request("GET", "/api/v1/agent-versions", params={"project_id": project_id} if project_id else None)["items"]

    async def agent_operations(self, agent_id: str, *, hours: int = 24) -> dict:
        """KPIs, 24-bucket series, percentiles, rollout timeline and sibling deployments for one agent."""
        if not 1 <= hours <= 168:
            raise ValueError("hours must be 1-168")
        return await self._request("GET", f"/api/v1/agents/{quote(agent_id, safe='')}/operations", params={"hours": hours})

    async def create_connection(
        self, name: str, type: str, secret_ref: str, *, endpoint: str = ""
    ) -> Connection:
        data = await self._request(
            "POST",
            "/api/v1/connections",
            json={"name": name, "type": type, "secret_ref": secret_ref, "endpoint": endpoint},
        )
        return Connection.model_validate(data)

    async def test_connection(self, connection_id: str) -> Connection:
        return Connection.model_validate(
            await self._request("POST", f"/api/v1/connections/{connection_id}/test", json={})
        )

    async def readiness(self) -> Readiness:
        return Readiness.model_validate(await self._request("GET", "/api/v1/onboarding/readiness"))

    async def audit_events(self) -> list[AuditEvent]:
        return [AuditEvent.model_validate(item) for item in await self._page("/api/v1/audit")]

    async def _page(self, path: str) -> list[dict]:
        return await self._request("GET", path)["items"]

    async def _request(self, method: str, path: str, **kwargs: object):
        response = await self._send(method, path, **kwargs)
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    async def _send(self, method: str, path: str, **kwargs: object) -> httpx.Response:
        """Send with retries (see ``RetryPolicy``); raise a typed error on failure."""
        request = self._client.build_request(method, path, **kwargs)
        attempt = 1
        while True:
            try:
                response = await self._client.send(request)
            except httpx.TransportError as error:
                if attempt < self.retry.max_attempts and self.retry.should_retry_error(request, error):
                    await self._sleep(self.retry.delay(attempt))
                    attempt += 1
                    continue
                kind = KiongaTimeoutError if isinstance(error, httpx.TimeoutException) else KiongaConnectionError
                raise kind(f"{method} {request.url.path} failed after {attempt} attempt(s): {error}") from error
            if response.is_success:
                return response
            if attempt < self.retry.max_attempts and self.retry.should_retry_status(request, response):
                await response.aclose()
                await self._sleep(self.retry.delay(attempt, response))
                attempt += 1
                continue
            await response.aread()
            raise error_for_response(response)
