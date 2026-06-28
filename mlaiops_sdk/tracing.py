from __future__ import annotations

import functools
import os
import time
import uuid
from collections.abc import Callable
from typing import Any

import httpx


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
                _record_trace(
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


def _record_trace(payload: dict[str, Any]) -> None:
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
