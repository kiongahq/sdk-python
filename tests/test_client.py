import json

import httpx

from mlaiops_sdk import MLAIOpsClient


def handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/api/v1/health":
        return httpx.Response(200, json={"status": "ok", "service": "gateway"})
    if request.url.path == "/api/v1/projects" and request.method == "GET":
        return httpx.Response(
            200,
            json=[
                {
                    "id": "prj-1",
                    "name": "Demo",
                    "description": "Starter",
                    "template": "tabular-classification",
                    "namespace": "demo",
                    "status": "ready",
                    "created_at": "2026-01-01T00:00:00Z",
                }
            ],
        )
    if request.url.path == "/api/v1/projects":
        payload = json.loads(request.content)
        return httpx.Response(
            201,
            json={
                "id": "prj-2",
                "namespace": "churn",
                "status": "ready",
                "created_at": "2026-01-01T00:00:00Z",
                **payload,
            },
        )
    raise AssertionError(f"Unexpected request: {request.method} {request.url}")


def test_client_health_and_projects() -> None:
    with MLAIOpsClient(transport=httpx.MockTransport(handler)) as client:
        assert client.health()["status"] == "ok"
        assert client.list_projects()[0].namespace == "demo"
        project = client.create_project("Churn", description="Retention model")
        assert project.id == "prj-2"
        assert project.template == "tabular-classification"
