"""Gate tests for the LLM provider factory. No network, no keys."""

import pytest

from mlaiops_sdk.llm import MissingLLMConfiguration, MockChatModel, build_chat_model


def test_mock_backend_is_deterministic():
    model = build_chat_model("mock")
    first = model.invoke("hello world")
    second = build_chat_model("mock").invoke("hello world")
    assert first.content == second.content
    assert first.usage_metadata["input_tokens"] > 0
    assert first.usage_metadata["output_tokens"] > 0


def test_mock_scripted_responses_play_in_order():
    model = MockChatModel(responses=["first", "second"])
    assert model.invoke("a").content == "first"
    assert model.invoke("b").content == "second"
    assert model.invoke("c").content == "second"  # final response repeats


def test_mock_supports_bind_tools():
    model = MockChatModel()
    assert model.bind_tools([]) is model


def test_openai_requires_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(MissingLLMConfiguration, match="OPENAI_API_KEY"):
        build_chat_model("openai")


def test_anthropic_requires_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(MissingLLMConfiguration, match="ANTHROPIC_API_KEY"):
        build_chat_model("anthropic")


def test_openai_compatible_requires_base_url(monkeypatch):
    monkeypatch.delenv("MLAIOPS_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    with pytest.raises(MissingLLMConfiguration, match="base_url"):
        build_chat_model("openai-compatible")


def test_openai_compatible_builds_without_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    model = build_chat_model("openai-compatible", "llama3", base_url="http://proxy:8081/v1")
    assert model.openai_api_base == "http://proxy:8081/v1"


def test_backend_from_environment(monkeypatch):
    monkeypatch.setenv("MLAIOPS_LLM_BACKEND", "mock")
    model = build_chat_model()
    assert model._llm_type == "mlaiops-mock"


def test_unknown_backend_rejected():
    with pytest.raises(MissingLLMConfiguration, match="unknown"):
        build_chat_model("banana")
