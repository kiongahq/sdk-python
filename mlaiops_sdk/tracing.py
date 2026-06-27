from __future__ import annotations

import functools
import os
import time
import uuid
from collections.abc import Callable
from typing import Any


def trace_llm(project: str) -> Callable:
    """Attach portable trace metadata without binding application code to one vendor."""

    def decorate(function: Callable) -> Callable:
        @functools.wraps(function)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            trace_id = str(uuid.uuid4())
            started = time.perf_counter()
            previous = os.environ.get("MLAIOPS_TRACE_ID")
            os.environ["MLAIOPS_TRACE_ID"] = trace_id
            try:
                return function(*args, **kwargs)
            finally:
                _ = time.perf_counter() - started
                if previous is None:
                    os.environ.pop("MLAIOPS_TRACE_ID", None)
                else:
                    os.environ["MLAIOPS_TRACE_ID"] = previous

        wrapped.mlaiops_project = project
        return wrapped

    return decorate
