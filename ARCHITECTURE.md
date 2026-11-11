# Architecture

This document describes how the Python SDK is organized and how it fits into
Kionga. Read it before you add a module or change a public signature.

## Bird's-eye view

The SDK is one package, `kionga`, distributed as `kionga`. It has two
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
module `__getattr__`, so `import kionga` stays light.

## Code map

### `kionga/_async/` and `kionga/_sync/`

The HTTP client is written once, async, in `kionga/_async/`: `AsyncKiongaClient` in
`client.py`, with agent teams (`teams.py`), the feature catalog, storage and logs
(`data.py`), and server-sent event streams (`sse.py`) mixed in.
`scripts/unasync.py` generates `kionga/_sync/` from it by whole-word replacement
(`async def` to `def`, `httpx.AsyncClient` to `httpx.Client`, and so on), the way
httpcore does. Never edit `_sync` by hand: `tests/test_generated_sync.py` fails when
it doesn't match the source.

The base URL comes from the argument, then `KIONGA_URL`, then
`http://localhost:8080`; the bearer token from the argument or `KIONGA_TOKEN`. Every
request goes through `_send`. It retries what `RetryPolicy` allows, and raises a
`KiongaAPIError` subclass for an error status. `_request` returns the JSON body, or
`None` for an empty one, and `_page` unwraps the `items` array of list endpoints.
`_stream` reads server-sent events and reconnects with `Last-Event-ID`.

### `kionga/client.py`, `teams.py`, `data.py`, `sse.py`, `errors.py`, `retry.py`

The public import paths. `client.py` re-exports both clients. The other modules
hold what both clients share: response models, errors, the retry policy and pure
helpers. So `KiongaClient` and `AsyncKiongaClient` return the same classes.

### `kionga/transport.py`

The rule for where a bearer token may travel over plaintext HTTP: loopback and
container or cluster service names only, unless `allow_insecure_http` or
`KIONGA_ALLOW_INSECURE_HTTP=1` opts in. `KiongaClient` raises
`InsecureTransportError` at construction; `record_trace` skips the trace instead,
because tracing must never break the caller.

### `kionga/models.py`

Pydantic models for gateway responses: `Project`, `ProjectTemplate`,
`PipelineRun`, `PipelineDefinition`, `Function`, `Model`, `Agent`, `Tool`,
`Connection`, `AgentSession`, `AgentTrace`, `AuditEvent`, `Readiness` and their
parts. Where the gateway can encode an empty list as `null` (for example a
project's `capabilities`), a validator turns it into `[]`.

### `kionga/pipelines.py`

`Pipeline`, `PipelineStep`, `FunctionStep` and `ResourceRequirements`: build a
pipeline in Python. `Pipeline.add` rejects duplicate step names and dependencies on
steps not yet added. `Pipeline.definition` produces the request body for the
gateway's pipeline-definition API.

### `kionga/llm.py`

`build_chat_model(backend, model, ...)` returns a LangChain chat model for
`openai`, `anthropic`, `openai-compatible` or `mock`, reading `KIONGA_LLM_BACKEND`,
`KIONGA_LLM_MODEL`, `KIONGA_LLM_BASE_URL` and the provider keys from the
environment. `mock` is deterministic and is what gate tests use.

### `kionga/tools.py`

A process-wide tool registry keyed by `(name, version)`. `register_tool` is a
decorator that derives a JSON input schema from type hints; `to_langchain_tool` and
`langchain_tools` convert entries to LangChain tools.

### `kionga/tracing.py`

Two sinks on one code path: full traces go to Langfuse when `LANGFUSE_*` is
configured, and a compact summary always goes to the gateway
(`POST /api/v1/traces`). Sink failures are caught and dropped.

### `kionga/agents.py`

`get_langgraph_checkpointer` (PostgreSQL checkpoints for LangGraph) and
`AgentMemoryClient`, which stores long-term semantic memory in the pgvector table
`agent_memories` and reads structured entity features from the online feature
gateway. `HashingEmbedder` is a deterministic embedding for tests and offline use.
Semantic memory is scoped by tenant, agent and user: `remember` and `recall` fail
closed without a `user_id`, `recall(scope="session")` narrows to one session,
`scope="agent"` is an explicit opt-in, and `forget` serves deletion requests.
`_ensure_schema` adds the scope columns to older tables without touching their
rows; rows from before scoping have no owner, so only `scope="agent"` reads them.

### `kionga/agent_adapters.py`

The contract that lets the agent runtime serve frameworks other than LangGraph:
`agent_factory` marks a factory, and `CallableAdapter`, `AgnoAdapter` and
`NooaAdapter` expose `ainvoke` and `astream` like a compiled graph.

### `kionga/huggingface.py`

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
- **One client, written once.** Change `kionga/_async`, then run
  `python scripts/unasync.py`. The sync client is generated.
- **Retries never run work twice.** Only idempotent methods, requests with an
  `Idempotency-Key`, failed connects and 429 responses are repeated.
- **One route, one method.** Client methods mirror gateway routes; the gateway is
  the authority on validation and authorization.

## How it connects to other components

- **platform**: every client call goes to the gateway API. Inside a Kionga
  workspace, `KIONGA_URL` points to the workspace sidecar, which forwards to the
  gateway as the workspace owner.
- **agent-runtime** installs `kionga[runtime]` and uses `llm`, `tools`,
  `tracing`, `agents` and `agent_adapters` to serve agents.
- **workspace**: the Jupyter image installs `kionga[agents,huggingface]`.
- **trace-proxy**: the `openai-compatible` backend can point `KIONGA_LLM_BASE_URL`
  at it so LLM calls are traced.

Downstream repositories pin a release tag. A change here reaches them only after a
release and a pin bump.

## Cross-cutting concerns

- **Versioning.** `version` in `pyproject.toml` and the `vX.Y.Z` tag must match.
- **Testing.** Unit tests use `httpx.MockTransport` and the `mock` LLM; the pgvector
  round trip runs in its own CI job.
- **Security gates.** CI runs ruff, pytest, pip-audit, bandit and gitleaks through
  the shared Python workflow. See [CONTRIBUTING.md](CONTRIBUTING.md).
