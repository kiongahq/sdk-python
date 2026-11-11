"""Feature catalog, storage and logs against the gateway's routes."""
import json
from datetime import datetime, timezone

import httpx
import pytest

import kionga

VIEW = {"id": "profile@2", "name": "profile", "version": 2, "sha256": "abc", "entity": "entity_id",
        "fields": [{"name": "plan", "type": "string"}], "tags": ["customer"], "source": "s3://f/profile",
        "created_by": "me"}


def log(seq, message="line"):
    return {"sequence": seq, "ts": "2026-11-10T00:00:00Z", "source": "runner", "severity": "info", "message": message}


class Gateway:
    def __init__(self, routes):
        self.routes, self.seen = routes, []

    def __call__(self, request):
        self.seen.append(request)
        answer = self.routes[(request.method, request.url.raw_path.decode().split("?")[0])]
        return answer(request) if callable(answer) else answer

    def params(self, index=-1):
        return dict(self.seen[index].url.params)


def client(routes):
    gateway = Gateway(routes)
    c = kionga.KiongaClient("http://gateway", transport=httpx.MockTransport(gateway))
    c.slept = []
    c._sleep = c.slept.append
    return c, gateway


def ok(body, status=200):
    return httpx.Response(status, json=body)


def test_features_versions_and_lineage_are_typed_and_keep_unknown_fields():
    c, _ = client({
        ("GET", "/api/v1/features"): ok({"items": [VIEW], "total": 1}),
        ("GET", "/api/v1/features/profile/versions"): ok({"items": [VIEW, {**VIEW, "version": 1}]}),
        ("GET", "/api/v1/features/profile/lineage"): ok({"items": [{"id": "fmat-1", "view": "profile", "status": "succeeded",
                                                                   "entity_count": 3, "created_at": "2026-11-10T23:03:03Z"}], "total": 1}),
    })
    [view] = c.list_features()
    assert (view.name, view.version, view.fields[0]["type"], view.created_by) == ("profile", 2, "string", "me")
    assert [v.version for v in c.feature_versions("profile")] == [2, 1]
    [run] = c.feature_lineage("profile")
    assert run.status == "succeeded" and run.created_at.year == 2026


def test_applying_a_feature_view_sends_the_documented_body():
    c, gw = client({("POST", "/api/v1/features"): ok(VIEW, 201)})
    c.apply_feature_view("profile", entity="entity_id", fields=[{"name": "plan", "type": "string"}],
                         source="s3://f/profile", tags=["customer"], ttl_seconds=3600)
    assert json.loads(gw.seen[0].content) == {
        "name": "profile", "entity": "entity_id", "fields": [{"name": "plan", "type": "string"}],
        "source": "s3://f/profile", "tags": ["customer"], "ttl_seconds": 3600}


def test_feature_names_are_escaped():
    c, gw = client({("GET", "/api/v1/features/a%2Fb/versions"): ok({"items": []})})
    assert c.feature_versions("a/b") == []


def test_walking_storage_descends_into_every_prefix():
    listings = {
        "": {"objects": [{"key": "top.txt"}], "prefixes": ["a/", "b/"]},
        "a/": {"objects": [{"key": "a/1"}], "prefixes": ["a/deep/"]},
        "a/deep/": {"objects": [{"key": "a/deep/2"}], "prefixes": []},
        "b/": {"objects": [{"key": "b/3"}], "prefixes": []},
    }
    c, gw = client({("GET", "/api/v1/storage/objects"): lambda r: ok(listings[r.url.params["prefix"]])})
    assert [o["key"] for o in c.walk_storage("models")] == ["top.txt", "a/1", "a/deep/2", "b/3"]
    assert {r.url.params["bucket"] for r in gw.seen} == {"models"}


def test_buckets_and_previews():
    c, gw = client({("GET", "/api/v1/storage/buckets"): ok({"buckets": [{"name": "models"}]}),
                    ("GET", "/api/v1/storage/object"): ok({"key": "k", "preview": "abc"})})
    assert c.list_buckets() == [{"name": "models"}]
    assert c.preview_storage_object("models", "k")["preview"] == "abc"
    assert gw.params() == {"bucket": "models", "key": "k"}


def test_a_log_query_sends_only_the_filters_given():
    c, gw = client({("GET", "/api/v1/logs"): ok({"items": [log(5)]})})
    since = datetime(2026, 11, 1, tzinfo=timezone.utc)
    [entry] = c.query_logs(run_id="run-1", severities=["warn", "error"], sources="runner", text="timeout",
                           since=since, newest_first=True, limit=50)
    assert entry.sequence == 5
    assert gw.params() == {"run_id": "run-1", "severity": "warn,error", "source": "runner", "q": "timeout",
                           "since": "2026-11-01T00:00:00+00:00", "order": "desc", "limit": "50"}


@pytest.mark.parametrize("kwargs", [{"severities": ["fatal"]}, {"sources": "kubelet"},
                                    {"since": datetime(2026, 1, 1)}])
def test_a_filter_the_gateway_would_ignore_is_rejected_before_sending(kwargs):
    c, gw = client({})
    with pytest.raises(ValueError):
        c.query_logs(**kwargs)
    assert gw.seen == []


def test_iterating_logs_reads_every_page_even_when_one_comes_back_short():
    pages = iter([[log(1), log(2)], [log(3)], [log(4), log(5)], []])
    c, gw = client({("GET", "/api/v1/logs"): lambda r: ok({"items": next(pages)})})
    assert [e.sequence for e in c.iter_logs(run_id="r", page_size=2)] == [1, 2, 3, 4, 5]
    assert [r.url.params["after"] for r in gw.seen[1:]] == ["2", "3", "5"]
    assert gw.params()["limit"] == "2"


def test_following_logs_polls_for_new_lines():
    pages = iter([[log(1)], [], [log(2)]])
    c, gw = client({("GET", "/api/v1/logs"): lambda r: ok({"items": next(pages)})})
    stream = c.iter_logs(run_id="r", follow=True, poll_interval=0.25)
    assert [next(stream).sequence, next(stream).sequence] == [1, 2]
    assert c.slept == [0.25]


@pytest.mark.parametrize("size", [0, 1001])
def test_a_page_size_the_gateway_would_reset_is_rejected(size):
    c, _ = client({})
    with pytest.raises(ValueError):
        next(c.iter_logs(page_size=size))
