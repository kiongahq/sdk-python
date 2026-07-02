"""LLM tracing primitives.

Two sinks, one code path: full hierarchical traces go to Langfuse when it is
configured (``LANGFUSE_PUBLIC_KEY``/``LANGFUSE_SECRET_KEY``/``LANGFUSE_HOST``),
and a compact summary always goes to the platform gateway so sessions, token
usage, and cost stay visible in the console even without Langfuse.
Observability must never break the agent execution path, so every emit is
best-effort.
"""

from __future__ import annotations

import functools
import os
import time
import uuid
from collections.abc import Callable
from typing import Any

import httpx


def langfuse_handler(**kwargs: Any) -> Any | None:
    """Return a Langfuse LangChain callback handler, or None when unconfigured.

    Handles both langfuse v2 (``langfuse.callback``) and v3
    (``langfuse.langchain``) import paths.
    """
    if not os.environ.get("LANGFUSE_PUBLIC_KEY") or not os.environ.get("LANGFUSE_SECRET_KEY"):
        return None
    try:
        from langfuse.langchain import CallbackHandler  # langfuse >= 3
    except ImportError:
        try:
            from langfuse.callback import CallbackHandler  # langfuse 2.x
        except ImportError:
            return None
    try:
        return CallbackHandler(**kwargs)
    except Exception:
        return None


class LangfuseContext:
    """Context manager wiring Langfuse callbacks into LangChain/LangGraph runs.

    Usage::

        with LangfuseContext(project="support", session_id=session) as ctx:
            result = graph.invoke(state, config=ctx.langgraph_config)
            ctx.score(name="user_rating", value=4.5)
    """

    def __init__(
        self,
        project: str,
        *,
        session_id: str | None = None,
        user_id: str | None = None,
        agent_id: str | None = None,
    ) -> None:
        self.project = project
        self.session_id = session_id or str(uuid.uuid4())
        self.user_id = user_id or os.environ.get("MLAIOPS_USER_ID", "")
        self.agent_id = agent_id or os.environ.get("MLAIOPS_AGENT_ID", "unassigned")
        self.handler = langfuse_handler()
        self._started = 0.0
        self._scores: list[dict[str, Any]] = []

    @property
    def callbacks(self) -> list[Any]:
        return [self.handler] if self.handler is not None else []

    @property
    def langgraph_config(self) -> dict[str, Any]:
        config: dict[str, Any] = {
            "configurable": {"thread_id": self.session_id},
            "metadata": {
                "langfuse_session_id": self.session_id,
                "langfuse_user_id": self.user_id,
                "project": self.project,
            },
        }
        if self.handler is not None:
            config["callbacks"] = [self.handler]
        return config

    def score(self, *, name: str, value: float) -> None:
        self._scores.append({"name": name, "value": value})
        client = getattr(self.handler, "langfuse", None)
        if client is not None:
            try:
                client.score(name=name, value=value)
            except Exception:
                pass

    def __enter__(self) -> "LangfuseContext":
        self._started = time.perf_counter()
        return self

    def __exit__(self, exc_type: type | None, *_: object) -> None:
        duration_ms = int((time.perf_counter() - self._started) * 1000)
        record_trace(
            {
                "agent_id": self.agent_id,
                "session_id": self.session_id,
                "user_id": self.user_id,
                "name": self.project,
                "status": "failed" if exc_type else "succeeded",
                "current_node": "",
                "duration_ms": duration_ms,
                "input_tokens": 0,
                "output_tokens": 0,
                "cost_usd": 0,
                "metadata": {"project": self.project, "scores": self._scores},
            }
        )
        client = getattr(self.handler, "langfuse", None)
        if client is not None:
            try:
                client.flush()
            except Exception:
                pass


def trace_llm(project: str) -> Callable:
    """Attach portable trace metadata without binding application code to one vendor."""

    def decorate(function: Callable) -> Callable:
        @functools.wraps(function)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            trace_id = str(uuid.uuid4())
            started = time.perf_counter()
            previous = os.environ.get("MLAIOPS_TRACE_ID")
            os.environ["MLAIOPS_TRACE_ID"] = trace_id
            status = "succeeded"
            try:
                return function(*args, **kwargs)
            except Exception:
                status = "failed"
                raise
            finally:
                duration_ms = int((time.perf_counter() - started) * 1000)
                record_trace(
                    {
                        "agent_id": os.environ.get("MLAIOPS_AGENT_ID", "unassigned"),
                        "session_id": os.environ.get("MLAIOPS_SESSION_ID", trace_id),
                        "user_id": os.environ.get("MLAIOPS_USER_ID", ""),
                        "name": function.__name__,
                        "status": status,
                        "current_node": function.__name__,
                        "duration_ms": duration_ms,
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "cost_usd": 0,
                        "metadata": {"project": project, "trace_id": trace_id},
                    }
                )
                if previous is None:
                    os.environ.pop("MLAIOPS_TRACE_ID", None)
                else:
                    os.environ["MLAIOPS_TRACE_ID"] = previous

        wrapped.mlaiops_project = project
        return wrapped

    return decorate


def record_trace(payload: dict[str, Any]) -> None:
    """Best-effort trace summary delivery to the platform gateway."""
    gateway = os.environ.get("MLAIOPS_URL")
    if not gateway:
        return
    headers = {}
    if token := os.environ.get("MLAIOPS_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    try:
        httpx.post(
            f"{gateway.rstrip('/')}/api/v1/traces",
            json=payload,
            headers=headers,
            timeout=2,
        )
    except httpx.HTTPError:
        # Observability must never break the model or agent execution path.
        return


# Backwards-compatible private alias (pre-1.0 callers).
_record_trace = record_trace
