"""A bearer token is never sent over plaintext HTTP to a host off this machine
or outside the container network, unless the caller opts in explicitly."""

import logging

import httpx
import pytest

from kionga import InsecureTransportError, KiongaClient
from kionga.tracing import record_trace
from kionga.transport import is_local_host


def ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"status": "ok", "auth": request.headers.get("authorization", "")})


def client(url, **kwargs):
    return KiongaClient(url, transport=httpx.MockTransport(ok), **kwargs)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("KIONGA_ALLOW_INSECURE_HTTP", "KIONGA_TOKEN", "KIONGA_URL"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("url", [
    "http://gateway.example.com",
    "http://gateway.example.com:8080/",
    "http://192.168.1.20:8080",
    "http://10.0.0.5",
    "http://8.8.8.8",
    "http://[2001:db8::1]:8080",
    "http://127.0.0.1.nip.io",
    "http://localhost.example.com",
    "http://kionga-gateway.kionga-system:8080",
    "HTTP://Gateway.Example.COM",
])
def test_token_over_plaintext_to_a_remote_host_raises(url):
    with pytest.raises(InsecureTransportError, match="plaintext HTTP"):
        client(url, token="t")


def test_token_from_the_environment_is_checked_too(monkeypatch):
    monkeypatch.setenv("KIONGA_TOKEN", "t")
    monkeypatch.setenv("KIONGA_URL", "http://gateway.example.com")
    with pytest.raises(InsecureTransportError):
        KiongaClient(transport=httpx.MockTransport(ok))


@pytest.mark.parametrize("url", [
    "http://localhost:8080",
    "http://127.0.0.1:9000",
    "http://127.10.20.30",
    "http://[::1]:8080",
    "http://[::ffff:127.0.0.1]:8080",
    "http://LOCALHOST.:8080",
    "http://kionga.localhost",
    "http://gateway:8080",                       # Compose service name
    "http://kionga-gateway.kionga.svc:8080",      # Helm in-cluster URL
    "http://gateway.kionga.svc.cluster.local",
    "https://gateway.example.com",
])
def test_local_and_https_urls_keep_working(url):
    with client(url, token="t") as c:
        assert c.health()["auth"] == "Bearer t"


def test_plaintext_without_a_token_is_allowed():
    with client("http://gateway.example.com") as c:
        assert c.health()["auth"] == ""


def test_explicit_opt_out_sends_and_warns(caplog):
    with caplog.at_level(logging.WARNING, logger="kionga"):
        with client("http://gateway.example.com", token="t", allow_insecure_http=True) as c:
            assert c.health()["auth"] == "Bearer t"
    assert "plaintext HTTP to gateway.example.com" in caplog.text


@pytest.mark.parametrize("value", ["1", "true", "YES"])
def test_environment_opt_out_sends_and_warns(monkeypatch, caplog, value):
    monkeypatch.setenv("KIONGA_ALLOW_INSECURE_HTTP", value)
    with caplog.at_level(logging.WARNING, logger="kionga"):
        client("http://gateway.example.com", token="t").close()
    assert "KIONGA_ALLOW_INSECURE_HTTP" in caplog.text


@pytest.mark.parametrize("value", ["0", "", "false", "no"])
def test_environment_opt_out_needs_a_true_value(monkeypatch, value):
    monkeypatch.setenv("KIONGA_ALLOW_INSECURE_HTTP", value)
    with pytest.raises(InsecureTransportError):
        client("http://gateway.example.com", token="t")


def test_explicit_false_overrides_the_environment(monkeypatch):
    monkeypatch.setenv("KIONGA_ALLOW_INSECURE_HTTP", "1")
    with pytest.raises(InsecureTransportError):
        client("http://gateway.example.com", token="t", allow_insecure_http=False)


def test_error_message_never_contains_the_token():
    with pytest.raises(InsecureTransportError) as raised:
        client("http://gateway.example.com", token="super-secret-token")
    assert "super-secret-token" not in str(raised.value)


# ---- record_trace ----------------------------------------------------------------

class Posts:
    def __init__(self):
        self.calls = []

    def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return httpx.Response(201)


@pytest.fixture
def posts(monkeypatch):
    recorder = Posts()
    monkeypatch.setattr(httpx, "post", recorder)
    return recorder


def test_record_trace_never_sends_the_token_to_an_insecure_remote_url(monkeypatch, posts, caplog):
    monkeypatch.setenv("KIONGA_URL", "http://gateway.example.com")
    monkeypatch.setenv("KIONGA_TOKEN", "t")
    with caplog.at_level(logging.WARNING, logger="kionga"):
        record_trace({"agent_id": "a", "session_id": "s"})  # must not raise
    assert all("authorization" not in {k.lower() for k in kw.get("headers", {})} for _, kw in posts.calls)
    assert posts.calls == []
    assert "trace not sent" in caplog.text


@pytest.mark.parametrize("url", ["http://gateway:8080", "http://127.0.0.1:8890", "https://gateway.example.com"])
def test_record_trace_sends_the_token_to_trusted_urls(monkeypatch, posts, url):
    monkeypatch.setenv("KIONGA_URL", url)
    monkeypatch.setenv("KIONGA_TOKEN", "t")
    record_trace({"agent_id": "a", "session_id": "s"})
    (sent_url, kwargs), = posts.calls
    assert sent_url == f"{url}/api/v1/traces" and kwargs["headers"]["Authorization"] == "Bearer t"


def test_record_trace_honors_the_opt_out(monkeypatch, posts):
    monkeypatch.setenv("KIONGA_URL", "http://gateway.example.com")
    monkeypatch.setenv("KIONGA_TOKEN", "t")
    monkeypatch.setenv("KIONGA_ALLOW_INSECURE_HTTP", "1")
    record_trace({"agent_id": "a", "session_id": "s"})
    assert posts.calls[0][1]["headers"]["Authorization"] == "Bearer t"


def test_record_trace_without_a_token_is_unchanged(monkeypatch, posts):
    monkeypatch.setenv("KIONGA_URL", "http://gateway.example.com")
    record_trace({"agent_id": "a", "session_id": "s"})
    assert posts.calls[0][1]["headers"] == {}


@pytest.mark.parametrize("host,expected", [
    ("", False), ("example.com", False), ("svc", True), ("evil-svc.com", False),
    ("x.svc.evil.com", False), ("::1", True), ("0.0.0.0", False),
])
def test_is_local_host_edges(host, expected):
    assert is_local_host(host) is expected
