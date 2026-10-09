# sdk-python

The Python SDK for [Kionga](https://github.com/kiongahq). Use it from notebooks,
scripts and agents to create projects, submit and watch pipeline runs, register and
deploy models, and build agents with memory, tools, tracing and a pluggable LLM.

```python
from mlaiops_sdk import MLAIOpsClient

with MLAIOpsClient() as client:
    project = client.create_project("customer-churn", template="production-ml")
    run = client.submit_pipeline(project.id)
    print(run.id, run.status)
```

Inside a Kionga notebook or browser IDE, `MLAIOPS_URL` points to a loopback
workspace helper. It authenticates to the internal gateway as the workspace owner;
the gateway checks the user's current access grants on every request. No personal
API key is needed in either workspace. Outside Kionga (for example, in a local IDE),
set `MLAIOPS_URL` to your deployed gateway and supply a scoped personal key via
`MLAIOPS_TOKEN` from a secret manager or OS environment.

| Module | What it gives you |
| --- | --- |
| `client` | `MLAIOpsClient`: typed calls to the Kionga gateway API |
| `models` | Pydantic models for projects, runs, models, agents, tools and more |
| `pipelines` | Build pipeline definitions in Python |
| `agents` | Agent memory (pgvector) and LangGraph checkpoint helpers |
| `llm` | `build_chat_model()`: OpenAI, Anthropic, any OpenAI-compatible server (vLLM, the Kionga trace-proxy), or a deterministic `mock` for tests |
| `tools` | Tool registry for agents |
| `tracing` | Langfuse tracing context |
| `huggingface` | Hub search and pinned-revision downloads |
| `agent_adapters` | Contract for Agno, NOOA and custom agent frameworks |

## Install

Not on PyPI yet. Install a tagged release from GitHub:

```bash
pip install "mlaiops-sdk @ https://github.com/kiongahq/sdk-python/archive/refs/tags/v0.2.0.tar.gz"
pip install "mlaiops-sdk[agents] @ https://github.com/kiongahq/sdk-python/archive/refs/tags/v0.2.0.tar.gz"
```

Extras: `agents` (LangGraph, LangChain, Langfuse, psycopg), `runtime` (agents plus
FastAPI/uvicorn, used by [agent-runtime](https://github.com/kiongahq/agent-runtime)),
`huggingface`, `agno`, `nooa`, `dev`.

Inside a Kionga Jupyter workspace the SDK is already installed.

## Develop

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
python -m pytest -q && ruff check .
```

`tests/test_agents.py` has one integration test that runs when `TEST_MEMORY_DSN`
points at a PostgreSQL database with pgvector.

Releases: tag `vX.Y.Z` matching `version` in `pyproject.toml`; CI builds the wheel and
sdist and attaches them to a GitHub release.

Full guide: [Python modules](https://kiongahq.github.io/docs/modules/python/).
