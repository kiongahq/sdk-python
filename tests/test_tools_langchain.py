"""Gate tests for tool registry -> LangChain conversion."""

import pytest

from mlaiops_sdk.tools import get_tool, langchain_tools, register_tool, to_langchain_tool


@register_tool(
    name="echo_test_tool",
    version="1.0",
    description="Echo the given text back",
    tags=["test"],
)
def echo_test_tool(text: str) -> str:
    return f"echo:{text}"


@register_tool(name="echo_test_tool", version="2.0", description="Echo v2", tags=["test"])
def echo_test_tool_v2(text: str) -> str:
    return f"echo2:{text}"


def test_get_tool_latest_version_wins():
    assert get_tool("echo_test_tool").version == "2.0"
    assert get_tool("echo_test_tool", "1.0").version == "1.0"


def test_get_tool_unknown_raises():
    with pytest.raises(KeyError, match="not registered"):
        get_tool("nope")


def test_to_langchain_tool_invokes_underlying_function():
    tool = to_langchain_tool(get_tool("echo_test_tool", "1.0"))
    assert tool.name == "echo_test_tool"
    assert tool.invoke({"text": "hi"}) == "echo:hi"


def test_langchain_tools_resolves_names():
    tools = langchain_tools(["echo_test_tool"])
    assert [tool.name for tool in tools] == ["echo_test_tool"]
