from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, get_type_hints

from pydantic import TypeAdapter


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    version: str
    description: str
    tags: tuple[str, ...]
    input_schema: dict[str, Any]
    function: Callable


_registry: dict[tuple[str, str], ToolDefinition] = {}


def register_tool(
    *,
    name: str,
    version: str,
    description: str,
    tags: list[str] | None = None,
) -> Callable:
    """Register a typed callable and derive its JSON input schema."""

    def decorate(function: Callable) -> Callable:
        signature = inspect.signature(function)
        hints = get_type_hints(function)
        properties: dict[str, Any] = {}
        required: list[str] = []
        for parameter_name, parameter in signature.parameters.items():
            annotation = hints.get(parameter_name, Any)
            properties[parameter_name] = TypeAdapter(annotation).json_schema()
            if parameter.default is inspect.Parameter.empty:
                required.append(parameter_name)
        definition = ToolDefinition(
            name=name,
            version=version,
            description=description,
            tags=tuple(tags or []),
            input_schema={"type": "object", "properties": properties, "required": required},
            function=function,
        )
        key = (name, version)
        if key in _registry:
            raise ValueError(f"tool {name}@{version} is already registered")
        _registry[key] = definition
        return function

    return decorate


def registered_tools() -> list[ToolDefinition]:
    return list(_registry.values())


def get_tool(name: str, version: str | None = None) -> ToolDefinition:
    """Resolve a registered tool by name; latest registered version wins."""
    if version is not None:
        try:
            return _registry[(name, version)]
        except KeyError:
            raise KeyError(f"tool {name}@{version} is not registered") from None
    matches = [definition for key, definition in _registry.items() if key[0] == name]
    if not matches:
        raise KeyError(f"tool {name} is not registered")
    return max(matches, key=lambda definition: definition.version)


def to_langchain_tool(definition: ToolDefinition):
    """Convert a registered tool into a LangChain ``StructuredTool``."""
    from langchain_core.tools import StructuredTool

    return StructuredTool.from_function(
        func=definition.function,
        name=definition.name,
        description=definition.description,
    )


def langchain_tools(names: list[str] | None = None) -> list:
    """LangChain tools for the named registrations (all tools when omitted)."""
    if names is None:
        return [to_langchain_tool(definition) for definition in registered_tools()]
    return [to_langchain_tool(get_tool(name)) for name in names]
