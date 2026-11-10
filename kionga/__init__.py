"""Typed notebook and automation entry point for the Kionga platform."""

from .client import KiongaClient
from .errors import (
    AuthenticationError,
    BadRequestError,
    ConflictError,
    KiongaAPIError,
    KiongaConnectionError,
    KiongaError,
    KiongaTimeoutError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    ServerError,
    ValidationError,
)
from .retry import RetryPolicy
from .teams import AgentDefinition, AgentTeam, TeamEvent, TeamRun, TeamRunTimeout, TeamWorker
from .transport import InsecureTransportError
from .models import (
    Agent,
    AgentAutoscaling,
    AgentResources,
    AgentSession,
    AgentTrace,
    AuditEvent,
    Connection,
    Function,
    GitRepository,
    Model,
    PipelineRun,
    PipelineDefinition,
    Project,
    ProjectTemplate,
    Readiness,
    Tool,
)

__all__ = [
    "AgentDefinition",
    "AgentTeam",
    "TeamEvent",
    "TeamRun",
    "TeamRunTimeout",
    "TeamWorker",
    "AuthenticationError",
    "BadRequestError",
    "ConflictError",
    "KiongaAPIError",
    "KiongaConnectionError",
    "KiongaError",
    "KiongaTimeoutError",
    "NotFoundError",
    "PermissionDeniedError",
    "RateLimitError",
    "RetryPolicy",
    "ServerError",
    "ValidationError",
    "Agent",
    "AgentAutoscaling",
    "AgentResources",
    "AgentMemoryClient",
    "AgentSession",
    "AgentTrace",
    "AuditEvent",
    "Connection",
    "Function",
    "GitRepository",
    "InsecureTransportError",
    "LangfuseContext",
    "KiongaClient",
    "Model",
    "PipelineRun",
    "PipelineDefinition",
    "Project",
    "ProjectTemplate",
    "Readiness",
    "Tool",
    "build_chat_model",
    "get_langgraph_checkpointer",
    "register_tool",
    "trace_llm",
]

_LAZY = {
    "AgentMemoryClient": ("kionga.agents", "AgentMemoryClient"),
    "get_langgraph_checkpointer": ("kionga.agents", "get_langgraph_checkpointer"),
    "build_chat_model": ("kionga.llm", "build_chat_model"),
    "LangfuseContext": ("kionga.tracing", "LangfuseContext"),
    "trace_llm": ("kionga.tracing", "trace_llm"),
    "register_tool": ("kionga.tools", "register_tool"),
}


def __getattr__(name: str):
    """Lazy exports keep the core import light: heavy agent dependencies load
    only when the corresponding symbol is used."""
    try:
        module_name, attribute = _LAZY[name]
    except KeyError:
        raise AttributeError(f"module 'kionga' has no attribute {name!r}") from None
    import importlib

    return getattr(importlib.import_module(module_name), attribute)
