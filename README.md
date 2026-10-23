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

The package is not on PyPI. Install a tagged release from GitHub (Python 3.11 or
later):

```bash
pip install "mlaiops-sdk @ https://github.com/kiongahq/sdk-python/archive/refs/tags/v0.2.0.tar.gz"

# with the agent modules (LangGraph, LangChain, Langfuse, psycopg)
pip install "mlaiops-sdk[agents] @ https://github.com/kiongahq/sdk-python/archive/refs/tags/v0.2.0.tar.gz"
```

Other extras: `runtime` (agents plus FastAPI and uvicorn), `huggingface`, `agno` and
`nooa`.

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
