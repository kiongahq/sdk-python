"""Client contract for the project-scoped Langflow and knowledge APIs."""

import httpx
import pytest

from mlaiops_sdk import MLAIOpsClient


def test_discovery_and_invocation_keep_credentials_on_gateway():
    seen = []

    def route(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.headers["authorization"] == "Bearer user-token"
        path = request.url.path
        if path == "/api/v1/knowledge-bases":
            return httpx.Response(200, json={"items": [{"id": "kb-1", "project_id": "prj-1", "backend": "qdrant"}]})
        if path == "/api/v1/datasets":
            return httpx.Response(200, json={"items": [{"id": "ds-1", "project_id": "prj-1", "prefix": "training/"}]})
        if path == "/api/v1/datasets/ds-1/objects":
            assert request.url.params["prefix"] == "churn/"
            return httpx.Response(200, json={"objects": [{"key": "training/churn/part.csv"}]})
        if path == "/api/v1/datasets/ds-1/object":
            assert request.url.params["key"] == "training/churn/part.csv"
            return httpx.Response(200, json={"content": "rows"})
        if path == "/api/v1/registered-storage":
            return httpx.Response(200, json={"items": [{"id": "st-1", "bucket": "archive"}]})
        if path == "/api/v1/registered-storage/st-1/objects":
            assert request.url.params["prefix"] == "reports/"
            return httpx.Response(200, json={"objects": [{"key": "reports/a.txt"}]})
        if path == "/api/v1/registered-storage/st-1/object":
            assert request.url.params["key"] == "reports/a.txt"
            return httpx.Response(200, json={"content": "hello"})
        if path == "/api/v1/knowledge-bases/kb-1/search":
            assert request.content == b'{"vector":[0.1,0.2],"limit":3}'
            return httpx.Response(200, json={"items": [{"text": "matched"}]})
        if path == "/api/v1/knowledge-bases/kb-1/chunks":
            return httpx.Response(201, json={"id": "chunk-1"})
        if path == "/api/v1/agent-api-connections":
            return httpx.Response(200, json={"items": [{"id": "api-1", "project_id": "prj-1"}]})
        if path == "/api/v1/agent-api-connections/api-1/invoke":
            assert request.content == b'{"question":"hello"}'
            return httpx.Response(200, json={"answer": "world"})
        raise AssertionError(path)

    with MLAIOpsClient(token="user-token", transport=httpx.MockTransport(route)) as client:
        assert client.list_knowledge_bases()[0]["id"] == "kb-1"
        assert client.list_datasets()[0]["id"] == "ds-1"
        assert client.list_dataset_objects("ds-1", prefix="churn/")["objects"][0]["key"] == "training/churn/part.csv"
        assert client.preview_dataset_object("ds-1", "training/churn/part.csv")["content"] == "rows"
        assert client.list_registered_storage()[0]["id"] == "st-1"
        assert client.list_registered_objects("st-1", prefix="reports/")["objects"][0]["key"] == "reports/a.txt"
        assert client.preview_registered_object("st-1", "reports/a.txt")["content"] == "hello"
        assert client.search_knowledge_base("kb-1", [0.1, 0.2], limit=3)[0]["text"] == "matched"
        assert client.add_knowledge_chunk("kb-1", "document", [0.1, 0.2])["id"] == "chunk-1"
        assert client.list_agent_api_connections()[0]["id"] == "api-1"
        assert client.invoke_agent_api_connection("api-1", {"question": "hello"})["answer"] == "world"
    assert all(b"secret_ref" not in request.content for request in seen)


def test_snapshot_workflow_preserves_revision_and_stable_route():
    operations = []

    def route(request: httpx.Request) -> httpx.Response:
        operations.append((request.method, request.url.path, request.content))
        path = request.url.path
        if path.endswith("/flows"):
            return httpx.Response(200, json={"items": [{"id": "flow-1"}]})
        if path.endswith("/snapshots") and request.method == "POST":
            return httpx.Response(200, json={"id": "lfs-1", "revision": 2})
        if path.endswith("/publish"):
            return httpx.Response(202, json={"id": "agent-2", "snapshot_revision": 2})
        if path.endswith("/activate"):
            return httpx.Response(200, json={"agent_id": "agent-2"})
        if path.endswith("/invoke"):
            return httpx.Response(200, json={"reply": "ok"})
        if path == "/api/v1/langflow-snapshots":
            return httpx.Response(200, json={"items": [{"id": "lfs-1"}]})
        return httpx.Response(200, json={"id": "lfs-1", "revision": 2})

    with MLAIOpsClient(transport=httpx.MockTransport(route)) as client:
        assert client.list_langflow_flows("workspace-1")[0]["id"] == "flow-1"
        assert client.capture_langflow_flow("workspace-1", "flow-1", "prj-1", snapshot_id="lfs-1", revision=1)["revision"] == 2
        assert client.list_langflow_snapshots()[0]["id"] == "lfs-1"
        assert client.get_langflow_snapshot("lfs-1")["revision"] == 2
        assert client.publish_langflow_snapshot("lfs-1", revision=2, max_replicas=3)["id"] == "agent-2"
        assert client.activate_langflow_deployment("lfs-1", "agent-2")["agent_id"] == "agent-2"
        assert client.invoke_langflow_snapshot("lfs-1", "hello")["reply"] == "ok"
    assert b'"revision":1' in operations[1][2]
    assert b'"max_replicas":3' in operations[4][2]
    assert operations[-1][1] == "/api/v1/langflow-snapshots/lfs-1/invoke"


def test_denied_resource_is_not_retried_or_disclosed():
    def denied(_: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "not_found"})

    with MLAIOpsClient(transport=httpx.MockTransport(denied)) as client:
        with pytest.raises(httpx.HTTPStatusError) as error:
            client.search_knowledge_base("another-project", [0.1])
    assert error.value.response.status_code == 404
