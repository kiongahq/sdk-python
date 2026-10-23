# Architecture

This document describes how the Python SDK is organized and how it fits into
Kionga. Read it before you add a module or change a public signature.

## Bird's-eye view

The SDK is one package, `mlaiops_sdk`, distributed as `mlaiops-sdk`. It has two
layers:

- **The client layer** (`client`, `models`, `pipelines`) depends only on `httpx`
  and `pydantic`. It wraps the Kionga gateway's REST API (`/api/v1/...`) in typed
  methods and Pydantic models.
- **The agent layer** (`llm`, `tools`, `tracing`, `agents`, `agent_adapters`,
  `huggingface`) needs the optional extras. It gives agent code a chat model
  factory, a tool registry, tracing, memory and framework adapters, all configured
  from the environment.

The package root imports only the client layer. Agent-layer symbols exported from
the root (`build_chat_model`, `register_tool`, `trace_llm`, `LangfuseContext`,
`AgentMemoryClient`, `get_langgraph_checkpointer`) load on first use through a
module `__getattr__`, so `import mlaiops_sdk` stays light.

## Code map

### `mlaiops_sdk/client.py`

`MLAIOpsClient`, a context manager around one `httpx.Client`. The base URL comes
from the argument, then `MLAIOPS_URL`, then `http://localhost:8080`; the bearer
token from the argument or `MLAIOPS_TOKEN`. Each public method maps to one gateway
route. `_request` raises `httpx.HTTPStatusError` for non-2xx responses and returns
`None` for `204`; `_page` unwraps the `items` array of list endpoints.

### `mlaiops_sdk/models.py`

Pydantic models for gateway responses: `Project`, `ProjectTemplate`,
`PipelineRun`, `PipelineDefinition`, `Function`, `Model`, `Agent`, `Tool`,
`Connection`, `AgentSession`, `AgentTrace`, `AuditEvent`, `Readiness` and their
parts. Where the gateway can encode an empty list as `null` (for example a
project's `capabilities`), a validator turns it into `[]`.

### `mlaiops_sdk/pipelines.py`

`Pipeline`, `PipelineStep`, `FunctionStep` and `ResourceRequirements`: build a
pipeline in Python. `Pipeline.add` rejects duplicate step names and dependencies on
steps not yet added. `Pipeline.definition` produces the request body for the
gateway's pipeline-definition API.

### `mlaiops_sdk/llm.py`

`build_chat_model(backend, model, ...)` returns a LangChain chat model for
`openai`, `anthropic`, `openai-compatible` or `mock`, reading `MLAIOPS_LLM_BACKEND`,
`MLAIOPS_LLM_MODEL`, `MLAIOPS_LLM_BASE_URL` and the provider keys from the
environment. `mock` is deterministic and is what gate tests use.

### `mlaiops_sdk/tools.py`

A process-wide tool registry keyed by `(name, version)`. `register_tool` is a
decorator that derives a JSON input schema from type hints; `to_langchain_tool` and
`langchain_tools` convert entries to LangChain tools.

### `mlaiops_sdk/tracing.py`

Two sinks on one code path: full traces go to Langfuse when `LANGFUSE_*` is
configured, and a compact summary always goes to the gateway
(`POST /api/v1/traces`). Sink failures are caught and dropped.

### `mlaiops_sdk/agents.py`

`get_langgraph_checkpointer` (PostgreSQL checkpoints for LangGraph) and
`AgentMemoryClient`, which stores long-term semantic memory in the pgvector table
`agent_memories` and reads structured entity features from the online feature
gateway. `HashingEmbedder` is a deterministic embedding for tests and offline use.

### `mlaiops_sdk/agent_adapters.py`

The contract that lets the agent runtime serve frameworks other than LangGraph:
`agent_factory` marks a factory, and `CallableAdapter`, `AgnoAdapter` and
`NooaAdapter` expose `ainvoke` and `astream` like a compiled graph.

### `mlaiops_sdk/huggingface.py`

Hugging Face Hub access for notebooks, IDEs and training jobs: `connect` and
`download_model`, which accepts only a commit-pinned `hf://<repo>@<sha>` artifact
URI from the registry and downloads that snapshot without executing model code.

### `examples/`, `notebooks/`, `tests/`

`examples/quickstart.py` creates a project and submits a run. `notebooks/eda.ipynb`
is a seed notebook (excluded from ruff). `tests/` holds one test module per area.

## Invariants

- **No credentials in code.** Gateway tokens, database DSNs and provider keys come
  from arguments or the environment, never from defaults in the package.
- **Observability never breaks the caller.** Tracing and reporting failures are
  caught and dropped.
- **Light core import.** The client layer must import without any optional extra
  installed.
- **One route, one method.** Client methods mirror gateway routes; the gateway is
  the authority on validation and authorization.

## How it connects to other components

- **platform**: every client call goes to the gateway API. Inside a Kionga
  workspace, `MLAIOPS_URL` points to the workspace sidecar, which forwards to the
  gateway as the workspace owner.
- **agent-runtime** installs `mlaiops-sdk[runtime]` and uses `llm`, `tools`,
  `tracing`, `agents` and `agent_adapters` to serve agents.
- **workspace**: the Jupyter image installs `mlaiops-sdk[agents,huggingface]`.
- **trace-proxy**: the `openai-compatible` backend can point `MLAIOPS_LLM_BASE_URL`
  at it so LLM calls are traced.

Downstream repositories pin a release tag. A change here reaches them only after a
release and a pin bump.

## Cross-cutting concerns

- **Versioning.** `version` in `pyproject.toml` and the `vX.Y.Z` tag must match.
- **Testing.** Unit tests use `httpx.MockTransport` and the `mock` LLM; the pgvector
  round trip runs in its own CI job.
- **Security gates.** CI runs ruff, pytest, pip-audit, bandit and gitleaks through
  the shared Python workflow. See [CONTRIBUTING.md](CONTRIBUTING.md).
