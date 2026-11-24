"""The kionga command: apps subcommands against a mocked gateway."""
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

import kionga
from kionga.cli import main, parse_since

ROOT = Path(__file__).resolve().parent.parent
BASE = "/api/v1/applications"
APP = {"name": "billing", "project_id": "p-1", "image": "ghcr.io/acme/billing:1.4",
       "replicas": {"min": 0, "max": 4}, "scaling": {"metric": "requests", "target": 20}, "visibility": "sso",
       "status": "ready", "url": "https://billing.example.net", "ready_replicas": 1}


def run(argv, routes, capsys):
    seen = []

    def handler(request):
        seen.append(request)
        answer = routes[(request.method, request.url.raw_path.decode().split("?")[0])]
        if isinstance(answer, list):
            answer = answer.pop(0) if len(answer) > 1 else answer[0]
        return answer

    def factory(url):
        c = kionga.KiongaClient(url or "http://gateway", transport=httpx.MockTransport(handler))
        c._sleep = lambda seconds: None
        return c

    code = main(argv, client_factory=factory)
    out, err = capsys.readouterr()
    return code, out, err, seen


def ok(body, status=200):
    return httpx.Response(status, json=body)


def test_deploy_builds_the_request_from_flags_and_prints_the_application(capsys):
    code, out, _, seen = run(
        ["apps", "deploy", "billing", "--project", "p-1", "--image", "ghcr.io/acme/billing:1.4",
         "--env", "MODE=prod", "--env", "URL=a=b", "--secret", "billing-db", "--min-replicas", "0",
         "--max-replicas", "4", "--scale-on", "requests", "--target", "20", "--health-path", "/healthz",
         "--cpu", "500m"],
        {("POST", BASE): ok(APP, 201)}, capsys)
    assert code == 0
    assert json.loads(seen[0].content) == {
        "project_id": "p-1", "name": "billing", "image": "ghcr.io/acme/billing:1.4",
        "env": {"MODE": "prod", "URL": "a=b"}, "secret_refs": ["billing-db"], "resources": {"cpu": "500m"},
        "replicas": {"min": 0, "max": 4}, "scaling": {"metric": "requests", "target": 20},
        "health_path": "/healthz"}
    lines = out.splitlines()
    assert "URL:        https://billing.example.net" in lines
    assert "Scaling:    requests target 20" in lines


def test_run_as_user_flags_set_keep_and_clear_the_user(capsys):
    code, _, _, seen = run(["apps", "deploy", "billing", "--project", "p-1", "--image", "i", "--run-as-user", "1000"],
                           {("POST", BASE): ok({**APP, "run_as_user": 1000}, 201)}, capsys)
    assert code == 0 and json.loads(seen[0].content)["run_as_user"] == 1000
    routes = {("GET", f"{BASE}/billing"): ok({**APP, "run_as_user": 1000}), ("PUT", f"{BASE}/billing"): ok(APP, 202)}
    code, _, _, seen = run(["apps", "update", "billing", "--image", "ghcr.io/acme/billing:1.5"], routes, capsys)
    assert code == 0 and json.loads(seen[-1].content)["run_as_user"] == 1000, "an update keeps it"
    code, _, _, seen = run(["apps", "update", "billing", "--run-as-user", "2000"], routes, capsys)
    assert code == 0 and json.loads(seen[-1].content)["run_as_user"] == 2000
    code, _, _, seen = run(["apps", "update", "billing", "--no-run-as-user"], routes, capsys)
    assert code == 0 and seen[-1].method == "PUT" and "run_as_user" not in json.loads(seen[-1].content)


def test_run_as_user_flags_refuse_root_and_contradictions(capsys):
    code, _, err, seen = run(["apps", "deploy", "billing", "--project", "p-1", "--image", "i", "--run-as-user", "0"],
                             {}, capsys)
    assert code == 1 and "never run as root" in err and seen == []
    for argv in (["apps", "update", "billing", "--run-as-user", "1000", "--no-run-as-user"],
                 ["apps", "deploy", "billing", "--project", "p-1", "--image", "i", "--no-run-as-user"],
                 ["apps", "deploy", "billing", "--project", "p-1", "--image", "i", "--run-as-user", "root"]):
        with pytest.raises(SystemExit) as stopped:
            run(argv, {}, capsys)
        assert stopped.value.code == 2
        capsys.readouterr()


def test_deploy_from_git_with_wait_polls_until_ready(capsys):
    building = {**APP, "name": "docs", "status": "pending", "message": "building image"}
    code, out, err, seen = run(
        ["apps", "deploy", "docs", "--project", "p-1", "--git", "https://github.com/acme/docs", "--ref", "main",
         "--wait", "--timeout", "60"],
        {("POST", BASE): ok(building, 201),
         ("GET", f"{BASE}/docs"): [ok(building), ok({**APP, "name": "docs"})]}, capsys)
    assert code == 0
    assert json.loads(seen[0].content)["source"] == {"git": "https://github.com/acme/docs", "ref": "main"}
    assert "Waiting for docs" in err and "Phase:      ready" in out


def test_a_failed_deploy_prints_the_reason_and_exits_1(capsys):
    failed = {**APP, "status": "failed", "message": "CrashLoopBackOff: exit code 1"}
    code, _, err, _ = run(["apps", "redeploy", "billing", "--wait"],
                          {("POST", f"{BASE}/billing/redeploy"): ok(failed, 202),
                           ("GET", f"{BASE}/billing"): ok(failed)}, capsys)
    assert code == 1
    assert err.splitlines()[-1] == "kionga: application billing failed: CrashLoopBackOff: exit code 1"


def test_compose_tells_the_user_applications_need_kubernetes(capsys):
    code, _, err, _ = run(["apps", "list"], {("GET", BASE): ok(
        {"error": "not_configured", "message": "This install runs on Compose.",
         "steps": ["Install Kionga on Kubernetes with the Helm chart."]}, 409)}, capsys)
    assert code == 1
    assert err.strip() == ("kionga: Applications run only on a Kubernetes install of Kionga. "
                           "This install runs on Compose. To enable them: (1) Install Kionga on "
                           "Kubernetes with the Helm chart.")


def test_invalid_flags_fail_before_sending(capsys):
    code, _, err, seen = run(["apps", "deploy", "Billing", "--project", "p", "--image", "i"], {}, capsys)
    assert (code, seen) == (1, []) and "DNS label" in err
    code, _, err, _ = run(["apps", "deploy", "b", "--project", "p", "--image", "i", "--scale-on", "cpu"],
                          {}, capsys)
    assert code == 1 and "--scale-on and --target go together" in err
    code, _, err, _ = run(["apps", "deploy", "b", "--project", "p", "--image", "i", "--env", "NOVALUE"],
                          {}, capsys)
    assert code == 1 and "KEY=VALUE" in err
    code, _, err, _ = run(["apps", "deploy", "b", "--project", "p", "--image", "i", "--ref", "v1"], {}, capsys)
    assert code == 1 and "need --git" in err


def test_image_and_git_are_mutually_exclusive_and_one_is_required(capsys):
    with pytest.raises(SystemExit) as caught:
        main(["apps", "deploy", "b", "--project", "p"])
    assert caught.value.code == 2
    with pytest.raises(SystemExit):
        main(["apps", "deploy", "b", "--project", "p", "--image", "i", "--git", "g"])
    capsys.readouterr()


def test_update_changes_the_flags_given_and_keeps_the_rest(capsys):
    code, _, _, seen = run(["apps", "update", "billing", "--image", "ghcr.io/acme/billing:1.5"],
                           {("GET", f"{BASE}/billing"): ok(APP), ("PUT", f"{BASE}/billing"): ok(APP, 202)}, capsys)
    assert code == 0 and [r.method for r in seen] == ["GET", "PUT"]
    assert json.loads(seen[1].content) == {
        "project_id": "p-1", "name": "billing", "image": "ghcr.io/acme/billing:1.5",
        "replicas": {"min": 0, "max": 4}, "scaling": {"metric": "requests", "target": 20}, "visibility": "sso"}


def test_list_prints_a_table_and_json(capsys):
    code, out, _, _ = run(["apps", "list"], {("GET", BASE): ok({"items": [APP]})}, capsys)
    assert code == 0
    header, row = out.splitlines()
    assert header.split() == ["NAME", "PHASE", "READY", "URL"]
    assert row.split() == ["billing", "ready", "1/4", "https://billing.example.net"]
    _, out, _, _ = run(["apps", "list", "--json"], {("GET", BASE): ok({"items": [APP]})}, capsys)
    assert json.loads(out)[0]["name"] == "billing"


def test_get_events_and_logs(capsys):
    routes = {
        ("GET", f"{BASE}/billing"): ok(APP),
        ("GET", f"{BASE}/billing/events"): ok({"items": [
            {"type": "Warning", "reason": "BackOff", "message": "restarting", "timestamp": "2026-11-19T00:00:00Z"}]}),
        ("GET", f"{BASE}/billing/logs"): ok({"items": [
            {"pod": "billing-0", "container": "app", "lines": "started\nlistening on 8080\n"},
            {"pod": "billing-1", "container": "app", "lines": "", "error": "container creating"}]}),
    }
    _, out, _, _ = run(["apps", "get", "billing", "--json"], routes, capsys)
    assert json.loads(out)["status"]["url"] == "https://billing.example.net"
    _, out, _, _ = run(["apps", "get", "billing"], routes, capsys)
    assert "Phase:      ready" in out and "Ready:      1/4" in out
    _, out, _, _ = run(["apps", "events", "billing"], routes, capsys)
    assert "BackOff" in out and "restarting" in out
    code, out, err, seen = run(["apps", "logs", "billing", "--tail", "5", "--container", "app"], routes, capsys)
    assert code == 0 and out == "[billing-0] started\n[billing-0] listening on 8080\n"
    assert err == "kionga: billing-1: container creating\n"
    assert seen[0].url.params["tail"] == "5" and seen[0].url.params["container"] == "app"
    code, _, err, _ = run(["apps", "logs", "billing", "--tail", "0"], routes, capsys)
    assert code == 1 and "1 to 2000" in err


def test_delete_needs_yes_without_a_terminal(capsys, monkeypatch):
    monkeypatch.setattr(sys, "stdin", open("/dev/null"))
    routes = {("DELETE", f"{BASE}/billing"): httpx.Response(204)}
    code, _, err, seen = run(["apps", "delete", "billing"], routes, capsys)
    assert code == 1 and "--yes" in err and seen == []
    code, out, _, seen = run(["apps", "delete", "billing", "--yes"], routes, capsys)
    assert code == 0 and out == "Deleted billing\n" and len(seen) == 1


def test_delete_asks_in_a_terminal(capsys, monkeypatch):
    class Terminal:
        def isatty(self):
            return True

    monkeypatch.setattr(sys, "stdin", Terminal())
    routes = {("DELETE", f"{BASE}/billing"): httpx.Response(204)}
    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    code, out, _, seen = run(["apps", "delete", "billing"], routes, capsys)
    assert (code, out, seen) == (0, "Not deleted.\n", [])
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    code, _, _, seen = run(["apps", "delete", "billing"], routes, capsys)
    assert code == 0 and len(seen) == 1


def test_python_dash_m_runs_the_same_command():
    result = subprocess.run([sys.executable, "-m", "kionga", "apps", "--help"], cwd=ROOT,
                            capture_output=True, text=True)
    assert result.returncode == 0
    for command in ("deploy", "update", "list", "get", "logs", "events", "redeploy", "delete"):
        assert command in result.stdout


def test_the_console_script_is_declared():
    assert 'kionga = "kionga.cli:main"' in (ROOT / "pyproject.toml").read_text()


def test_update_wait_passes_the_new_generation(capsys):
    stale = {**APP, "generation": 5, "observed_generation": 4}
    fresh = {**APP, "generation": 5, "observed_generation": 5}
    code, out, _, seen = run(["apps", "update", "billing", "--image", "i:2", "--wait"],
                             {("PUT", f"{BASE}/billing"): ok(stale, 202),
                              ("GET", f"{BASE}/billing"): [ok(stale), ok(stale), ok(fresh)]}, capsys)
    assert code == 0 and [r.method for r in seen] == ["GET", "PUT", "GET", "GET"]


def stream(*blocks):
    return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, content="".join(blocks).encode())


def log_event(time, line, pod="billing-0"):
    body = json.dumps({"pod": pod, "container": "app", "time": time, "line": line})
    return f"id: {time}\nevent: log\ndata: {body}\n\n"


def test_logs_follow_streams_lines_until_the_pods_finish(capsys):
    route = {("GET", f"{BASE}/billing/logs"): [
        stream(log_event("2026-11-24T10:00:00.1Z", "started"), log_event("2026-11-24T10:00:00.2Z", "up", "billing-1"),
               'event: end\ndata: {"reason":"max_seconds reached; reconnect"}\n\n'),
        stream(log_event("2026-11-24T10:00:01Z", "ready"),
               'event: end\ndata: {"reason":"all pod log streams closed"}\n\n')]}
    code, out, err, seen = run(["apps", "logs", "billing", "-f", "--tail", "5", "--container", "app"], route, capsys)
    assert code == 0
    assert out == "[billing-0] started\n[billing-1] up\n[billing-0] ready\n"
    assert err == "kionga: log stream ended: all pod log streams closed\n"
    assert seen[0].url.params["follow"] == "true" and seen[0].url.params["tail"] == "5"
    assert seen[1].headers["Last-Event-ID"] == "2026-11-24T09:59:55.199999999Z"


def test_logs_since_takes_a_timestamp_or_a_duration(capsys):
    done = stream('event: end\ndata: {"reason":"no pods are running"}\n\n')
    code, _, err, seen = run(["apps", "logs", "billing", "--follow", "--since", "2026-11-24T09:00:00Z"],
                             {("GET", f"{BASE}/billing/logs"): done}, capsys)
    assert code == 0 and seen[0].url.params["since"] == "2026-11-24T09:00:00Z"
    assert err == "kionga: log stream ended: no pods are running\n"
    code, _, _, seen = run(["apps", "logs", "billing", "--follow", "--since", "10m"],
                           {("GET", f"{BASE}/billing/logs"): done}, capsys)
    assert code == 0 and seen[0].url.params["since"].endswith("Z") and "T" in seen[0].url.params["since"]


def test_since_durations_are_exact():
    now = datetime(2026, 11, 24, 10, 0, 0, tzinfo=timezone.utc)
    assert parse_since("30s", now=now) == "2026-11-24T09:59:30.000000Z"
    assert parse_since("2h", now=now) == "2026-11-24T08:00:00.000000Z"
    assert parse_since("1d", now=now) == "2026-11-23T10:00:00.000000Z"
    assert parse_since(" 2026-11-24T09:00:00Z ") == "2026-11-24T09:00:00Z"
    with pytest.raises(ValueError, match="further back"):
        parse_since("9999999999999d")


def test_since_without_follow_and_a_bad_since_fail_with_a_message(capsys):
    code, _, err, seen = run(["apps", "logs", "billing", "--since", "10m"], {}, capsys)
    assert code == 1 and "--since needs --follow" in err and seen == []
    code, _, err, seen = run(["apps", "logs", "billing", "-f", "--since", "yesterday"], {}, capsys)
    assert code == 1 and "RFC 3339" in err and seen == []


def test_follow_on_compose_says_applications_need_kubernetes(capsys):
    body = {"error": "not_configured", "message": "Applications run only on Kubernetes.", "steps": ["Use Helm."]}
    code, out, err, seen = run(["apps", "logs", "billing", "-f"],
                               {("GET", f"{BASE}/billing/logs"): ok(body, 409)}, capsys)
    assert code == 1 and out == "" and "Kubernetes" in err and len(seen) == 1


class FakeFollow:
    """A client whose stream raises what a terminal does."""

    def __init__(self, error):
        self.error, self.closed = error, False

    def follow_application_logs(self, name, **kwargs):
        try:
            yield kionga.ApplicationLogLine(pod="billing-0", line="first")
            raise self.error
        finally:
            self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_ctrl_c_ends_a_follow_cleanly_and_closes_the_stream(capsys):
    fake = FakeFollow(KeyboardInterrupt())
    code = main(["apps", "logs", "billing", "-f"], client_factory=lambda url: fake)
    out, err = capsys.readouterr()
    assert code == 0 and out == "[billing-0] first\n" and "Traceback" not in err and fake.closed


def test_a_closed_pipe_ends_a_follow_quietly(capsys):
    fake = FakeFollow(BrokenPipeError())
    assert main(["apps", "logs", "billing", "-f"], client_factory=lambda url: fake) == 0
    assert fake.closed
    capsys.readouterr()
