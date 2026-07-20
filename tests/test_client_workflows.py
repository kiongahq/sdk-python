import json

import httpx

from mlaiops_sdk import MLAIOpsClient


def workflow_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/api/v1/pipelines/definitions":
        if request.method == "GET":
            return httpx.Response(200, json={"items": [], "total": 0})
        payload = json.loads(request.content)
        return httpx.Response(201, json={"id": "pipe-1", "created_at": "2026-07-20T00:00:00Z", "updated_at": "2026-07-20T00:00:00Z", **payload})
    if request.url.path == "/api/v1/functions" and request.method == "POST":
        payload = json.loads(request.content)
        return httpx.Response(202, json={"status": "deployed", "replicas": 0, "created_at": "2026-07-20T00:00:00Z", "updated_at": "2026-07-20T00:00:00Z", **payload})
    if request.url.path == "/api/v1/functions" and request.method == "GET":
        return httpx.Response(200, json={"configured": True, "items": [], "total": 0})
    if request.url.path == "/api/v1/functions/score-fn/invoke":
        return httpx.Response(200, json={"score": 0.91})
    if request.url.path == "/api/v1/functions/score-fn/invoke-async":
        return httpx.Response(202, json={"accepted": True, "call_id": "call-1"})
    if request.url.path == "/api/v1/functions/score-fn" and request.method == "DELETE":
        return httpx.Response(204)
    raise AssertionError(f"Unexpected request: {request.method} {request.url}")


def test_client_manages_function_flows() -> None:
    with MLAIOpsClient(transport=httpx.MockTransport(workflow_handler)) as client:
        function = client.deploy_function("prj-1", "score-fn", "ghcr.io/acme/score:1")
        assert function.memory == "512Mi"
        definition = client.create_pipeline_definition("prj-1", "score", "1", [{"name": "score", "kind": "function", "function": "score-fn", "resources": {}, "retries": 0}], execution_mode="functions")
        assert definition.execution_mode == "functions"
        assert client.invoke_function("score-fn", {"id": "evt-1"})["score"] == 0.91
        assert client.invoke_function_async("score-fn", {"id": "evt-2"})["call_id"] == "call-1"
        client.delete_function("score-fn")
