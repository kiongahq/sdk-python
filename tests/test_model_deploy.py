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


DEPLOYMENT = "/api/v1/models/mdl-1/deployment"


def deployment(**fields):
    return {"model_id": "mdl-1", "name": "churn", "version": "3", "status": "pending", "endpoint_url": "",
            "message": "", "canary_weight": 10, **fields}


def test_wait_reads_the_deployment_route_until_serving():
    answers = [deployment(message="waiting for the InferenceService"),
               deployment(status="serving", endpoint_url="http://churn.models.svc/v1/models/churn")]

    def handler(request, n):
        assert request.url.path == DEPLOYMENT
        return httpx.Response(200, json=answers[min(n, 2) - 1])

    c, _ = client(handler)
    served = c.wait_for_model_deployment("mdl-1", poll_interval=5)
    assert served.serving and served.endpoint_url.endswith("/v1/models/churn") and served.canary_weight == 10
    assert c.slept == [5]


def test_wait_raises_with_the_failure_message():
    c, _ = client(lambda request, n: httpx.Response(200, json=deployment(
        status="failed", message="artifact s3://models/churn/3 not found")))
    with pytest.raises(ModelDeploymentFailed) as caught:
        c.wait_for_model_deployment("mdl-1")
    assert caught.value.reason == "artifact s3://models/churn/3 not found"
    assert "not found" in str(caught.value)


@pytest.mark.parametrize("status", ["stopped", "retired", "rolled_back"])
def test_a_version_that_stopped_deploying_ends_the_wait(status):
    c, _ = client(lambda request, n: httpx.Response(200, json=deployment(status=status, message="replaced by 4")))
    with pytest.raises(ModelDeploymentFailed, match="replaced by 4"):
        c.wait_for_model_deployment("mdl-1")


def test_wait_times_out_with_the_last_status():
    c, _ = client(lambda request, n: httpx.Response(200, json=deployment(message="waiting for GPU")))
    with pytest.raises(ModelDeploymentTimeout) as caught:
        c.wait_for_model_deployment("mdl-1", timeout=0)
    assert isinstance(caught.value, TimeoutError)
    assert caught.value.deployment.status == "pending"
    assert "still pending after 0s: waiting for GPU" in str(caught.value)


def test_a_gateway_without_the_deployment_route_is_read_through_the_model_list():
    def handler(request, n):
        if request.url.path == DEPLOYMENT:
            return httpx.Response(404, json={"error": "not_found"})
        return httpx.Response(200, json={"items": [model(deployment_status="serving", endpoint_url="http://e")]})

    c, _ = client(handler)
    served = c.wait_for_model_deployment("mdl-1")
    assert (served.status, served.endpoint_url, served.model_id) == ("serving", "http://e", "mdl-1")


def test_get_model_raises_lookup_error_for_an_unknown_id():
    c, _ = client(lambda request, n: httpx.Response(200, json={"items": [MODEL]}))
    assert c.get_model("mdl-1").name == "churn"
    with pytest.raises(LookupError):
        c.get_model("mdl-x")


def test_model_records_surface_status_endpoint_and_message():
    old = Model.model_validate(model(deployment_status="deploying", canary_weight=0))
    assert not old.serving and not old.deployment_failed and old.deployment_reason == ""
    failed = Model.model_validate(model(deployment_status="failed", deployment_message="no artifact"))
    assert failed.deployment_failed and failed.deployment_reason == "no artifact"


def test_deploy_agent_sends_scaling_only_when_given():
    c, seen = client(lambda request, n: httpx.Response(201, json=AGENT))
    agent = c.deploy_agent("p-1", "support", "1", "i", "g", scaling={"metric": "requests", "target": 5})
    assert json.loads(seen[0].content)["scaling"] == {"metric": "requests", "target": 5}
    assert agent.scaling.metric == "requests" and agent.scaling.target == 5
    c.deploy_agent("p-1", "support", "1", "i", "g")
    assert "scaling" not in json.loads(seen[1].content)


@pytest.mark.parametrize("scaling", [
    {"metric": "gpu", "target": 5}, {"metric": "cpu", "target": 0}, {"metric": "cpu", "target": 101},
    {"metric": "requests", "target": 10001},
])
def test_agent_scaling_is_validated(scaling):
    c, seen = client(lambda request, n: httpx.Response(201, json=AGENT))
    with pytest.raises(ValueError):
        c.deploy_agent("p-1", "support", "1", "i", "g", scaling=scaling)
    assert seen == []


def test_version_deploys_send_no_scaling_because_the_gateway_rejects_unknown_fields():
    c, seen = client(lambda request, n: httpx.Response(202, json={"id": "agt-2"}))
    c.deploy_agent_version("ver-1", min_replicas=0, max_replicas=3)
    assert set(json.loads(seen[0].content)) == {"autoscaling", "resources", "require_evaluation"}


def test_agents_deployed_before_scaling_parse():
    agent = kionga.Agent.model_validate({**AGENT, "scaling": {"metric": "", "target": 0}})
    assert agent.scaling.metric == ""
    assert kionga.Agent.model_validate({**AGENT, "scaling": None}).scaling is None
