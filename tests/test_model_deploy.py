"""Model deployment status, canary weight and waiting; agent scaling fields."""
import json

import httpx
import pytest

import kionga
from kionga import Model, ModelDeploymentFailed, ModelDeploymentTimeout

MODEL = {
    "id": "mdl-1", "project_id": "p-1", "name": "churn", "version": "3", "stage": "production",
    "artifact_uri": "models:/churn/3", "metrics": {"auc": 0.91}, "created_at": "2026-11-19T00:00:00Z",
    "gate_status": "passed", "deployment_status": "pending", "canary_weight": 10, "endpoint_url": None,
}
AGENT = {
    "id": "agt-1", "project_id": "p-1", "name": "support", "version": "1", "image": "i", "graph_module": "g",
    "llm_backend": "mock", "status": "running", "replicas": 1, "canary_weight": 0, "tools": [],
    "created_at": "2026-11-19T00:00:00Z", "scaling": {"metric": "requests", "target": 5},
}


def model(**fields):
    return {**MODEL, **fields}


def client(handler):
    seen = []

    def record(request):
        seen.append(request)
        return handler(request, len(seen))

    c = kionga.KiongaClient("http://gateway", transport=httpx.MockTransport(record))
    c.slept = []
    c._sleep = c.slept.append
    return c, seen


def test_deploy_sends_the_canary_weight_and_surfaces_status_and_endpoint():
    c, seen = client(lambda request, n: httpx.Response(202, json=model()))
    deployed = c.deploy_model("mdl-1", canary_weight=10)
    assert json.loads(seen[0].content) == {"canary_weight": 10}
    assert (deployed.deployment_status, deployed.canary_weight, deployed.serving) == ("pending", 10, False)


@pytest.mark.parametrize("weight", [-1, 101])
def test_a_canary_weight_outside_0_to_100_is_rejected_before_sending(weight):
    c, seen = client(lambda request, n: httpx.Response(500))
    with pytest.raises(ValueError, match="0 to 100"):
        c.deploy_model("mdl-1", canary_weight=weight)
    assert seen == []


def test_wait_returns_the_serving_model_with_its_endpoint():
    answers = [model(), model(deployment_status="serving", endpoint_url="http://churn.models.svc/v1/models/churn")]

    def handler(request, n):
        other = model(id="mdl-2", deployment_status="serving")
        return httpx.Response(200, json={"items": [other, answers[min(n, 2) - 1]]})

    c, _ = client(handler)
    served = c.wait_for_model_deployment("mdl-1", poll_interval=5)
    assert served.serving and served.endpoint_url.endswith("/v1/models/churn")
    assert c.slept == [5]


def test_wait_raises_with_the_failure_reason():
    c, _ = client(lambda request, n: httpx.Response(200, json={"items": [
        model(deployment_status="failed", deployment_message="artifact s3://models/churn/3 not found")]}))
    with pytest.raises(ModelDeploymentFailed) as caught:
        c.wait_for_model_deployment("mdl-1")
    assert caught.value.reason == "artifact s3://models/churn/3 not found"
    assert "not found" in str(caught.value)


def test_wait_times_out_with_the_last_status():
    c, _ = client(lambda request, n: httpx.Response(200, json={"items": [model(deployment_error="waiting for GPU")]}))
    with pytest.raises(ModelDeploymentTimeout) as caught:
        c.wait_for_model_deployment("mdl-1", timeout=0)
    assert isinstance(caught.value, TimeoutError)
    assert caught.value.model.deployment_status == "pending"
    assert "still pending after 0s: waiting for GPU" in str(caught.value)


def test_get_model_raises_lookup_error_for_an_unknown_id():
    c, _ = client(lambda request, n: httpx.Response(200, json={"items": [MODEL]}))
    assert c.get_model("mdl-1").name == "churn"
    with pytest.raises(LookupError):
        c.get_model("mdl-x")


def test_older_gateway_statuses_still_parse():
    old = Model.model_validate(model(deployment_status="deploying", canary_weight=0))
    assert not old.serving and not old.deployment_failed and old.deployment_reason == ""


def test_deploy_agent_sends_scaling_only_when_given():
    c, seen = client(lambda request, n: httpx.Response(201, json=AGENT))
    agent = c.deploy_agent("p-1", "support", "1", "i", "g", scaling={"metric": "requests", "target": 5})
    assert json.loads(seen[0].content)["scaling"] == {"metric": "requests", "target": 5}
    assert agent.scaling.metric == "requests" and agent.scaling.target == 5
    c.deploy_agent("p-1", "support", "1", "i", "g")
    assert "scaling" not in json.loads(seen[1].content)


def test_agent_scaling_is_validated():
    c, seen = client(lambda request, n: httpx.Response(201, json=AGENT))
    with pytest.raises(ValueError):
        c.deploy_agent("p-1", "support", "1", "i", "g", scaling={"metric": "gpu", "target": 5})
    with pytest.raises(ValueError):
        c.deploy_agent_version("ver-1", scaling={"metric": "cpu", "target": 0})
    assert seen == []


def test_deploy_agent_version_passes_scaling():
    c, seen = client(lambda request, n: httpx.Response(202, json={"id": "agt-2"}))
    c.deploy_agent_version("ver-1", min_replicas=0, max_replicas=3, scaling=kionga.Scaling(metric="cpu", target=70))
    body = json.loads(seen[0].content)
    assert body["scaling"] == {"metric": "cpu", "target": 70}
    assert body["autoscaling"] == {"min_replicas": 0, "max_replicas": 3}


def test_agents_without_scaling_parse():
    agent = kionga.Agent.model_validate({**AGENT, "scaling": None})
    assert agent.scaling is None
