# sdk-python

[![CI](https://github.com/kiongahq/sdk-python/actions/workflows/ci.yml/badge.svg)](https://github.com/kiongahq/sdk-python/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENCE)

The Python SDK for Kionga: drive projects, pipelines, models and agents from notebooks, scripts and agent code.

## Background

Kionga is a self-hosted platform for machine learning, data and AI agents. This SDK
(`mlaiops_sdk`) is how Python code talks to it. `MLAIOpsClient` gives you typed
calls to the Kionga API for projects, pipeline runs, models, agents, tools and
connections. The optional agent modules add an LLM factory, a tool registry,
agent memory on pgvector, LangGraph checkpoints and Langfuse tracing.

The SDK comes preinstalled in Kionga's Jupyter workbench, and the agent runtime is
built on it.

## Install

The SDK requires Python 3.11 or later. Install a tagged release from GitHub:

```bash
pip install "mlaiops-sdk @ https://github.com/kiongahq/sdk-python/archive/refs/tags/v0.2.0.tar.gz"

# with the agent modules (LangGraph, LangChain, Langfuse, psycopg)
pip install "mlaiops-sdk[agents] @ https://github.com/kiongahq/sdk-python/archive/refs/tags/v0.2.0.tar.gz"
```

Other extras: `runtime` (agents plus FastAPI and uvicorn), `huggingface`, `agno` and
`nooa`.

PyPI publication is [on hold](https://github.com/kiongahq/sdk-python/issues/29)
until a Kionga email is available. Use the GitHub releases above; reviewed SDK
releases attach the verified wheel, source distribution and checksums.
After PyPI publication is enabled, `python -m pip install mlaiops-sdk` installs
the published distribution. Pin the selected version in your dependency file. See
[CONTRIBUTING.md](CONTRIBUTING.md#build-and-release) for trusted-publisher setup.

## Usage

```python
from mlaiops_sdk import MLAIOpsClient

with MLAIOpsClient() as client:
    project = client.create_project("customer-churn", template="production-ml")
    run = client.submit_pipeline(project.id)
    print(run.id, run.status)
```

The client reads the gateway address from `MLAIOPS_URL` (default
`http://localhost:8080`) and a personal API key from `MLAIOPS_TOKEN`. Inside a Kionga
notebook or browser IDE, `MLAIOPS_URL` already points to the workspace's
authenticated helper, so you need no key. Outside Kionga, create a key in
**Settings > API keys** and keep it in a secret manager or your environment.

The client refuses to send a key over plain `http://` to another machine, because
anyone on the network path could read it. `MLAIOpsClient(...)` raises
`InsecureTransportError`, and `record_trace` skips the trace and logs a warning.
Plain HTTP stays allowed for:

- loopback: `localhost`, `127.0.0.0/8` and `::1`;
- container and cluster service names: a single-label host such as `gateway`, or a
  Kubernetes name ending in `.svc` or `.svc.cluster.local`.

Use an `https://` URL everywhere else. To send the key over plain HTTP on a network
you trust, pass `allow_insecure_http=True` or set `MLAIOPS_ALLOW_INSECURE_HTTP=1`;
the SDK logs a warning each time.

Agent memory is isolated per user. Pass the authenticated user, never a value from
model output or a request body:

```python
from mlaiops_sdk.agents import AgentMemoryClient

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
