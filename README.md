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
`request_id`. These still subclass `httpx.HTTPStatusError`. If the gateway can't be
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
