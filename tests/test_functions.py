"""deploy_function: replica bounds and scaling trigger, as the gateway accepts them."""
import json

import httpx
import pytest

import kionga

FUNCTION = {"name": "resize", "project_id": "p-1", "image": "ghcr.io/acme/resize:1", "status": "scaled-to-zero",
            "replica_range": {"min": 0, "max": 5}, "scaling": {"metric": "requests", "target": 100},
            "message": "", "created_at": "2026-11-19T00:00:00Z"}


def client():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(202, json=FUNCTION)

    return kionga.KiongaClient("http://gateway", transport=httpx.MockTransport(handler)), seen


def test_without_bounds_or_scaling_the_body_is_unchanged_and_the_gateway_defaults_apply():
    c, seen = client()
    c.deploy_function("p-1", "resize", "ghcr.io/acme/resize:1")
    assert json.loads(seen[0].content) == {
        "project_id": "p-1", "name": "resize", "image": "ghcr.io/acme/resize:1", "cpu": "500m",
        "memory": "512Mi", "env_vars": {}, "annotations": {}}


def test_bounds_and_scaling_are_sent_and_read_back():
    c, seen = client()
    fn = c.deploy_function("p-1", "resize", "i", min_replicas=1, max_replicas=10,
                           scaling={"metric": "cpu", "target": 70})
    sent = json.loads(seen[0].content)
    assert sent["replicas"] == {"min": 1, "max": 10}
    assert sent["scaling"] == {"metric": "cpu", "target": 70}
    assert fn.replica_range == {"min": 0, "max": 5} and fn.scaling.metric == "requests"


@pytest.mark.parametrize("given, sent", [
    ({"max_replicas": 3}, {"min": 0, "max": 3}),
    ({"min_replicas": 2}, {"min": 2, "max": 5}),
    ({"min_replicas": 7}, {"min": 7, "max": 7}),
])
def test_one_bound_takes_the_gateway_default_for_the_other(given, sent):
    c, seen = client()
    c.deploy_function("p-1", "resize", "i", **given)
    assert json.loads(seen[0].content)["replicas"] == sent


@pytest.mark.parametrize("fields, message", [
    ({"min_replicas": -1}, "negative"),
    ({"max_replicas": 0}, "1 to 100"),
    ({"max_replicas": 101}, "1 to 100"),
    ({"min_replicas": 4, "max_replicas": 2}, "at least min_replicas"),
    ({"scaling": {"metric": "cpu", "target": 70}}, "at least one replica"),
    ({"scaling": {"metric": "cpu", "target": 70}, "min_replicas": 0, "max_replicas": 3}, "at least one replica"),
    ({"scaling": {"metric": "memory", "target": 5}}, "metric"),
    ({"scaling": {"metric": "requests", "target": -5}}, "1 to 10000"),
])
def test_invalid_bounds_and_scaling_are_rejected_before_sending(fields, message):
    c, seen = client()
    with pytest.raises(ValueError, match=message):
        c.deploy_function("p-1", "resize", "i", **fields)
    assert seen == []


def test_functions_from_older_gateways_still_parse():
    fn = kionga.Function.model_validate({"name": "f", "image": "i", "status": "deployed"})
    assert fn.replica_range is None and fn.scaling is None and fn.message == ""


@pytest.mark.parametrize("scaling, sent", [
    ({"metric": "requests"}, {"metric": "requests"}),
    ({"metric": "cpu", "target": 0}, {"metric": "cpu"}),
])
def test_a_scaling_rule_without_a_target_takes_the_gateway_default(scaling, sent):
    c, seen = client()
    c.deploy_function("p-1", "resize", "i", min_replicas=1, scaling=scaling)
    assert json.loads(seen[0].content)["scaling"] == sent
