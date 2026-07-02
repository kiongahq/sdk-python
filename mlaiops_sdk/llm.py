"""LLM provider factory.

Builds a LangChain ``BaseChatModel`` for any configured backend. Providers are
selected by name so agents swap between external APIs, an OpenAI-compatible
endpoint (the platform trace-proxy or a self-hosted server), and a deterministic
mock used by gate tests. Credentials come from the environment or Kubernetes
Secret mounts, never from source code.
"""

from __future__ import annotations

import os
from typing import Any

_ENV_BACKEND = "MLAIOPS_LLM_BACKEND"
_ENV_MODEL = "MLAIOPS_LLM_MODEL"
_ENV_BASE_URL = "MLAIOPS_LLM_BASE_URL"

_DEFAULT_MODELS = {
    "openai": "gpt-4o",
    "anthropic": "claude-sonnet-4-5",
    "openai-compatible": "default",
    "self-hosted": "default",
}


class MissingLLMConfiguration(RuntimeError):
    """Raised when a backend cannot be constructed from the environment."""


def build_chat_model(
    backend: str | None = None,
    model: str | None = None,
    *,
    base_url: str | None = None,
    api_key: str | None = None,
    temperature: float = 0.0,
    **kwargs: Any,
):
    """Return a LangChain chat model for the requested backend.

    Backends: ``openai``, ``anthropic``, ``openai-compatible`` (any server that
    speaks the OpenAI protocol, including the platform trace-proxy and vLLM),
    ``self-hosted`` (alias of ``openai-compatible``), and ``mock`` (offline,
    deterministic, for gate tests).
    """
    backend = (backend or os.environ.get(_ENV_BACKEND, "openai")).lower()
    model = model or os.environ.get(_ENV_MODEL) or _DEFAULT_MODELS.get(backend, "")
    if backend == "mock":
        return MockChatModel()
    if backend == "anthropic":
        try:
            from langchain_anthropic import ChatAnthropic
        except ImportError as error:  # pragma: no cover - environment guard
            raise MissingLLMConfiguration(
                "backend 'anthropic' requires langchain-anthropic; "
                "install mlaiops-sdk[agents]"
            ) from error
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise MissingLLMConfiguration("ANTHROPIC_API_KEY is not set")
        return ChatAnthropic(model=model, temperature=temperature, api_key=key, **kwargs)
    if backend in ("openai", "openai-compatible", "self-hosted"):
        try:
            from langchain_openai import ChatOpenAI
        except ImportError as error:  # pragma: no cover - environment guard
            raise MissingLLMConfiguration(
                "backend '%s' requires langchain-openai; install mlaiops-sdk[agents]" % backend
            ) from error
        url = base_url or os.environ.get(_ENV_BASE_URL) or os.environ.get("OPENAI_BASE_URL")
        key = api_key or os.environ.get("OPENAI_API_KEY")
        if backend == "openai" and not key:
            raise MissingLLMConfiguration("OPENAI_API_KEY is not set")
        if backend != "openai" and not url:
            raise MissingLLMConfiguration(
                "backend '%s' requires a base_url (MLAIOPS_LLM_BASE_URL)" % backend
            )
        return ChatOpenAI(
            model=model,
            temperature=temperature,
            api_key=key or "not-needed",
            base_url=url,
            **kwargs,
        )
    raise MissingLLMConfiguration(f"unknown llm backend {backend!r}")


def MockChatModel(responses: list[Any] | None = None):
    """Deterministic chat model for gate tests. No network, no keys.

    Replies with scripted responses in order, repeating the final one. A
    scripted entry is either a plain string reply or a tool-call spec
    ``{"tool_call": {"name": ..., "args": {...}}}`` so agent tool loops can be
    exercised offline. With no script it echoes a canned acknowledgement of
    the last human message.
    """
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult

    class _MockChatModel(BaseChatModel):
        script: list[Any] = []
        calls: int = 0

        @property
        def _llm_type(self) -> str:
            return "mlaiops-mock"

        def bind_tools(self, tools: Any, **_: Any) -> "_MockChatModel":
            return self

        def _generate(self, messages: Any, stop: Any = None, run_manager: Any = None,
                      **kwargs: Any) -> ChatResult:
            entry: Any
            if self.script:
                entry = self.script[min(self.calls, len(self.script) - 1)]
            else:
                last = messages[-1].content if messages else ""
                entry = f"[mock] acknowledged: {last}"
            self.calls += 1
            input_tokens = sum(len(str(m.content).split()) for m in messages)
            if isinstance(entry, dict) and "tool_call" in entry:
                call = entry["tool_call"]
                message = AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": call["name"],
                            "args": call.get("args", {}),
                            "id": f"mock-call-{self.calls}",
                            "type": "tool_call",
                        }
                    ],
                    usage_metadata={
                        "input_tokens": input_tokens,
                        "output_tokens": 1,
                        "total_tokens": 0,
                    },
                )
            else:
                text = str(entry)
                message = AIMessage(
                    content=text,
                    usage_metadata={
                        "input_tokens": input_tokens,
                        "output_tokens": len(text.split()),
                        "total_tokens": 0,
                    },
                )
            return ChatResult(generations=[ChatGeneration(message=message)])

    return _MockChatModel(script=list(responses or []))
