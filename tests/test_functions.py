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


def test_run_as_user_is_sent_when_given_and_read_back():
    c, seen = client()
    c.deploy_function("p-1", "resize", "i", run_as_user=1000)
    assert json.loads(seen[0].content)["run_as_user"] == 1000
    assert kionga.Function.model_validate({**FUNCTION, "run_as_user": 1000}).run_as_user == 1000
    assert kionga.Function.model_validate(FUNCTION).run_as_user is None


@pytest.mark.parametrize("value, message", [
    (0, "never run as root"), (-5, "1 to 2147483647"), (2147483648, "1 to 2147483647"), ("1000", "whole number"),
])
def test_a_run_as_user_the_gateway_would_refuse_is_rejected_before_sending(value, message):
    c, seen = client()
    with pytest.raises(ValueError, match=message):
        c.deploy_function("p-1", "resize", "i", run_as_user=value)
    assert seen == []


def test_visibility_and_labels_are_sent_so_a_redeploy_does_not_reset_them():
    # The gateway replaces the whole function on a deploy. Without these arguments a
    # redeploy from the SDK could only send the default, and a public function
    # became internal again.
    c, seen = client()
    c.deploy_function("p-1", "resize", "ghcr.io/acme/resize:2", visibility="public",
                      labels={"team": "media", "tier": ""})
    sent = json.loads(seen[0].content)
    assert sent["visibility"] == "public" and sent["labels"] == {"team": "media", "tier": ""}
    for visibility in ("internal", "sso"):
        c.deploy_function("p-1", "resize", "i", visibility=visibility)
        assert json.loads(seen[-1].content)["visibility"] == visibility
    c.deploy_function("p-1", "resize", "i", labels={})
    assert json.loads(seen[-1].content)["labels"] == {}


def test_a_function_reports_its_visibility_and_labels():
    fn = kionga.Function.model_validate({**FUNCTION, "visibility": "public", "labels": {"team": "media"}})
    assert fn.visibility == "public" and fn.labels == {"team": "media"}
    bare = kionga.Function.model_validate(FUNCTION)
    assert bare.visibility == "" and bare.labels == {}


@pytest.mark.parametrize("fields, message", [
    ({"visibility": "private"}, "visibility"), ({"visibility": ""}, "visibility"),
    ({"labels": ["team"]}, "labels"), ({"labels": {"team": 1}}, "labels"), ({"labels": {"": "x"}}, "labels"),
])
def test_an_invalid_visibility_or_labels_is_rejected_before_sending(fields, message):
    c, seen = client()
    with pytest.raises(ValueError, match=message):
        c.deploy_function("p-1", "resize", "i", **fields)
    assert seen == []
