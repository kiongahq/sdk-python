# sdk-python

[![CI](https://github.com/kiongahq/sdk-python/actions/workflows/ci.yml/badge.svg)](https://github.com/kiongahq/sdk-python/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENCE)

The Python SDK for Kionga: drive projects, pipelines, models and agents from notebooks, scripts and agent code.

## Background

Kionga is a self-hosted platform for machine learning, data and AI agents. This SDK
(`kionga`) is how Python code talks to it. `KiongaClient` gives you typed
calls to the Kionga API for projects, pipeline runs, models, agents, tools and
connections. The optional agent modules add an LLM factory, a tool registry,
agent memory on pgvector, LangGraph checkpoints and Langfuse tracing.

The SDK comes preinstalled in Kionga's Jupyter workbench, and the agent runtime is
built on it.

## Install

The SDK requires Python 3.11 or later. Install a tagged release from GitHub:

```bash
pip install "kionga @ https://github.com/kiongahq/sdk-python/archive/refs/tags/v0.2.0.tar.gz"

# with the agent modules (LangGraph, LangChain, Langfuse, psycopg)
pip install "kionga[agents] @ https://github.com/kiongahq/sdk-python/archive/refs/tags/v0.2.0.tar.gz"
```

Other extras: `runtime` (agents plus FastAPI and uvicorn), `huggingface`, `agno` and
`nooa`.

PyPI publication is [on hold](https://github.com/kiongahq/sdk-python/issues/29)
until a Kionga email is available. Use the GitHub releases above; reviewed SDK
releases attach the verified wheel, source distribution and checksums.
After PyPI publication is enabled, `python -m pip install kionga` installs
the published distribution. Pin the selected version in your dependency file. See
[CONTRIBUTING.md](CONTRIBUTING.md#build-and-release) for trusted-publisher setup.

## Usage

```python
from kionga import KiongaClient

with KiongaClient() as client:
    project = client.create_project("customer-churn", template="production-ml")
    run = client.submit_pipeline(project.id)
    print(run.id, run.status)
```

The client reads the gateway address from `KIONGA_URL` (default
`http://localhost:8080`) and a personal API key from `KIONGA_TOKEN`. Inside a Kionga
notebook or browser IDE, `KIONGA_URL` already points to the workspace's
authenticated helper, so you need no key. Outside Kionga, create a key in
**Settings > API keys** and keep it in a secret manager or your environment.

The client refuses to send a key over plain `http://` to another machine, because
anyone on the network path could read it. `KiongaClient(...)` raises
`InsecureTransportError`, and `record_trace` skips the trace and logs a warning.
Plain HTTP stays allowed for:

- loopback: `localhost`, `127.0.0.0/8` and `::1`;
- container and cluster service names: a single-label host such as `gateway`, or a
  Kubernetes name ending in `.svc` or `.svc.cluster.local`.

Use an `https://` URL everywhere else. To send the key over plain HTTP on a network
you trust, pass `allow_insecure_http=True` or set `KIONGA_ALLOW_INSECURE_HTTP=1`;
the SDK logs a warning each time.

### Errors and retries

Every error response raises a subclass of `KiongaAPIError`, such as `NotFoundError`,
`ConflictError` or `RateLimitError`, with the gateway's `code`, `message` and
`request_id`. These still subclass `httpx.HTTPStatusError`. A feature this install
has not enabled raises `NotConfiguredError`, which is also the error class for its
status, and is not retried. If the gateway can't be
reached, the client raises `KiongaConnectionError`, or `KiongaTimeoutError` for a
timeout.

The client retries 429, 502, 503 and 504 responses with jittered backoff, and waits
as long as `Retry-After` asks. It retries only when a repeat can't do the work twice:
for GET, PUT and DELETE, for a POST that carries an `Idempotency-Key`, after a
failed connect, and after a 429. Adjust or turn off retries with `RetryPolicy`:

```python
from kionga import KiongaClient, NotFoundError, RetryPolicy

client = KiongaClient(retry=RetryPolicy(max_attempts=5, max_backoff=10))
try:
    client.get_team_run("atr-missing")
except NotFoundError as error:
    print(error.code, error.message)
```

### Agent teams

```python
with KiongaClient() as client:
    fixer = client.create_agent_definition(project_id, "bug-fixer", instructions="Fix the bug.", model="llm")
    team = client.create_agent_team(project_id, "code-change", [fixer.id],
                                    coordinator={"instructions": "Plan, then delegate.", "model": "llm"})
    run = client.start_team_run(team.id, "Fix the failing date test")
    for event in client.team_run_events(run.id, follow=True):
        print(event.seq, event.type)
    print(client.get_team_run(run.id).status)
```

`update_agent_definition` and `update_agent_team` save on top of the revision you
read, and raise `ConflictError` if someone saved after you did. `start_team_run`
sends an idempotency key, so a retried start returns the same run. Only people
start team runs: a personal API key works, but the gateway refuses the workspace
credential a Kionga notebook uses and an agent run's credential, with
`PermissionDeniedError`.

`stream_team_run_events(run.id)` follows the same events over server-sent events
instead of polling, and ends with the run.

### Applications

Applications are your own services, run by Kionga as Kubernetes workloads and
served at `https://<name>.<install domain>`. An application is addressed by its
name. They need a Kubernetes install: on a Compose install every applications call
raises `ApplicationsNotConfiguredError`, which says so and lists the gateway's
enablement steps (`error.steps`).

```python
from kionga import ApplicationFailed, ApplicationTimeout, KiongaClient

with KiongaClient() as client:
    app = client.create_application(
        project_id, "billing",
        image="ghcr.io/acme/billing:1.4",        # or source={"git": url, "ref": "main"}
        port=8080, env={"MODE": "prod"}, secret_refs=["billing-db"],
        min_replicas=0, max_replicas=4,            # 0 scales to zero when idle
        scaling={"metric": "requests", "target": 20},
        health_path="/healthz", visibility="sso",  # "public" skips sign-in; "internal": no host
        run_as_user=1000,                          # only for an image that runs as root
    )
    try:
        app = client.wait_until_ready(app.name, timeout=600)
        print(app.url)
    except ApplicationFailed as error:              # failed: reason from status or events
        print(error.reason)
    except ApplicationTimeout as error:             # still starting: last known reason
        print(error.application.phase, error.reason)
    client.update_application(app.name, image="ghcr.io/acme/billing:1.5")
    for pod in client.application_logs(app.name, tail=100):
        for line in pod.messages:
            print(pod.pod, line)

    # Follow the logs as the pods write them (Ctrl-C or `break` stops):
    for entry in client.follow_application_logs(app.name, tail=20):
        print(entry.pod, entry.line)
```

Fields left out take the gateway's defaults: port 8080, 250m CPU and 256Mi memory,
1 to 3 replicas, `requests` scaling, health path `/` and `sso`. Scale to zero needs
`requests` scaling. `update_application` changes the fields you pass and keeps the
rest: the gateway replaces the whole configuration, so it reads the current one
first, and a change someone saves between that read and the write is overwritten.

`run_as_user` is the numeric user the containers run as, from 1 to 2147483647. Kionga
never runs a workload as root, so set it when the image runs as root or names its
user instead of numbering it; such an image does not start without it. 0 is refused,
by the client before anything is sent and by the gateway. `update_application` keeps
the user the application has; pass `run_as_user=` to change it, or
`run_as_user=kionga.CLEAR` to remove it so the image's own user applies (`None`
means "keep", as for every other field). `deploy_function` takes `run_as_user` too
(Kubernetes only). A function deploy replaces the whole function, so pass it on
every deploy that should keep it. `Application.run_as_user` and
`Function.run_as_user` report the value, `None` when it is not set.

Also `list_applications`, `get_application`, `redeploy_application`,
`application_events` (newest first) and `delete_application`. Logs are the recent
lines of each pod. Invalid fields, such as a name
that is not a DNS label or `max_replicas` below `min_replicas`, raise `ValueError`
before anything is sent.

The `kionga` command does the same from a terminal (`python -m kionga` works too):

```bash
kionga apps deploy billing --project "$PROJECT" --image ghcr.io/acme/billing:1.4 \
  --env MODE=prod --secret billing-db --min-replicas 0 --max-replicas 4 \
  --scale-on requests --target 20 --health-path /healthz --wait
kionga apps list
kionga apps get billing --json
kionga apps logs billing --tail 100
kionga apps logs billing --follow --since 10m   # Ctrl-C stops; --since takes a time or 30s, 10m, 2h, 1d
kionga apps events billing
kionga apps update billing --image ghcr.io/acme/billing:1.5 --wait
kionga apps update billing --run-as-user 1000   # --no-run-as-user removes it; deploy takes --run-as-user too
kionga apps redeploy billing --wait
kionga apps delete billing --yes
```

Errors print as `kionga: <reason>` and exit with status 1. `kionga apps logs
--follow` ends cleanly on Ctrl-C (status 0), prints `kionga: log stream ended:
<reason>` to stderr when the pods' logs finish, and needs `--follow` for `--since`.

#### Following logs

`follow_application_logs(name, tail=, container=, since=, max_seconds=, on_end=)`
yields `ApplicationLogLine` objects (`pod`, `container`, `time`, `line`) from the
gateway's Server-Sent Events stream, as a generator on `KiongaClient` and an async
iterator on `AsyncKiongaClient`. It starts with the last `tail` lines of each pod
(1 to 2000, default 200; a few more when several lines share the timestamp of the
first), or the lines after `since` (an RFC 3339 time or a timezone-aware
`datetime`). The gateway follows at most 20 pods and closes a stream
after `max_seconds` (1 to 3600, default 600); the client reopens it with the cursor
the gateway sent with the last line. The cursor records what each pod has delivered,
so every pod continues exactly after its own last line: no repeats and no gaps, even
when one pod delivers a line long after a newer line from another. Identical lines are
never merged: two pods logging the same text, or one pod logging it twice in the
same instant, each arrive. The gateway's own "log stream unavailable" notices carry
no timestamp, so they can't be placed and arrive every time they are sent.

A gateway from before the cursor sends a timestamp with each line instead, and the
client detects that from the id. It then reopens the stream from five seconds before
the newest line it delivered and drops the resend: still no repeats, but a line is
lost if the gateway delivers it more than five seconds late. If a gateway refuses a
cursor (an older replica during an upgrade), the client continues in this mode.

A dropped connection is retried with the client's
`RetryPolicy`. The iterator ends when the pods' logs are finished (no pods running,
or every log stream closed, for example after a rollout) and passes the gateway's
reason to `on_end`; follow again with `since` set to the last `line.time` to pick up
new pods. On a Compose install it raises `ApplicationsNotConfiguredError`.

### Model and agent deployments

`deploy_model(model_id, canary_weight=10)` returns as soon as the deployment is
recorded. A weight of 1 to 99 serves this version beside the one already serving;
0 or 100 replaces it. `model_deployment(model_id)` reads the live status:
`pending`, then `serving` with `endpoint_url`, or `failed` with `message`.
`wait_for_model_deployment` waits for that outcome and raises
`ModelDeploymentFailed` (also when the version is stopped, retired or rolled back)
or `ModelDeploymentTimeout`:

```python
client.deploy_model(model_id, canary_weight=10)
deployment = client.wait_for_model_deployment(model_id, timeout=600)
print(deployment.endpoint_url, deployment.canary_weight)
```

`deploy_agent` takes `scaling={"metric": "requests", "target": 10}` (concurrent
requests per replica) or `{"metric": "cpu", "target": 70}` (utilisation percent)
to choose what drives autoscaling; `Agent.scaling` reports it.
`deploy_agent_version(version_id, scaling=...)` takes the same rule when you deploy
a published agent design version (`requests` 1 to 10000, `cpu` 1 to 100).

On Kubernetes, `deploy_function` takes `min_replicas`, `max_replicas` (the gateway
defaults to 0 to 5, so a function scales to zero) and the same `scaling`; `cpu`
scaling needs `min_replicas` of 1 or more. `Function.replica_range`,
`Function.scaling` and `Function.message` report them. Compose installs ignore them.

### Async

`AsyncKiongaClient` has every method of `KiongaClient`, awaited, and returns the
same models and errors:

```python
from kionga import AsyncKiongaClient

async with AsyncKiongaClient() as client:
    run = await client.start_team_run(team_id, "Fix the failing date test")
    async for event in client.stream_team_run_events(run.id):
        print(event.seq, event.type)
```

### Features, storage and logs

```python
with KiongaClient() as client:
    for view in client.list_features():
        print(view.name, view.version, [run.status for run in client.feature_lineage(view.name)])
    for item in client.walk_storage("kionga-features"):
        print(item["key"])
    for entry in client.iter_logs(run_id=run_id, severities=["warn", "error"]):
        print(entry.sequence, entry.severity, entry.message)
    for entry in client.stream_logs(project_id=project_id):  # new lines, until you stop
        print(entry.message)
```

Log filters the gateway doesn't know, and timestamps without a timezone, raise
`ValueError` before anything is sent, because the gateway would ignore them and
return more than you asked for.

Agent memory is isolated per user. Pass the authenticated user, never a value from
model output or a request body:

```python
from kionga.agents import AgentMemoryClient

with AgentMemoryClient("support", session_id, user_id=user.id) as memory:
    memory.remember("Prefers email")
    memory.recall("contact preference")                   # this user's memories
    memory.recall("contact preference", scope="session")  # this session only
    memory.forget()                                       # delete this user's memories
```

`remember` and `recall` raise `MissingConfiguration` without a `user_id`.
`scope="agent"` reads or writes agent-wide memory and is an explicit opt-in. Rows
stored before this scoping have no owner: they stay in the table, and only
`scope="agent"` can read them.

## Worked example

[`notebooks/eda.ipynb`](notebooks/eda.ipynb) answers a question teams face every
time training runs again: **which model version should go to production?** It
explores every registered version against production, traces each one to its
training run and that run's logs, applies promotion rules you can edit, and then
promotes the winner and archives the version it replaces. It only changes
anything when you set `PROMOTE = True`.

## Documentation

- Product documentation: [Python SDK reference](https://kiongahq.github.io/docs/reference/python-sdk/)
  and [Build agents](https://kiongahq.github.io/docs/guides/agents/)
- Engineering handbook: [Python modules](https://kiongahq.github.io/docs/engineering/components/python-modules/)
- How this repository is laid out: [ARCHITECTURE.md](ARCHITECTURE.md)

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the development setup, the checks CI
runs, releases and the commit conventions.

## License

[MIT](LICENCE) © Kionga contributors
