"""Central typed ToolRegistry with strict schema & capability checks (Phase 7.1)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
import json
from typing import Any

from jarvis.tools.models import (
    ToolCall,
    ToolCapability,
    ToolExecutionContext,
    ToolExecutionError,
    ToolOutputLimitError,
    ToolRegistrationError,
    ToolSpec,
    ToolValidationError,
)

ToolHandler = Callable[
    [dict[str, Any], ToolExecutionContext], Awaitable[dict[str, Any]]
]


def _matches_type(value: Any, expected: type[Any] | tuple[type[Any], ...]) -> bool:
    if expected is int and isinstance(value, bool):
        return False
    return isinstance(value, expected)


class ToolRegistry:
    """Authoritative registry of JARVIS tools and typed contracts."""

    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}
        self._handlers: dict[str, ToolHandler] = {}

    def register(
        self,
        spec: ToolSpec,
        handler: ToolHandler,
        *,
        caller_is_agent: bool = False,
    ) -> None:
        """Register a tool. Agents are prohibited from registering/escalating tools."""
        if caller_is_agent:
            raise ToolRegistrationError(
                "Agents are not permitted to register or modify tool capabilities"
            )
        if not spec.tool_id or not spec.tool_id.strip():
            raise ToolRegistrationError("tool_id must be non-empty")
        if spec.tool_id in self._specs:
            raise ToolRegistrationError(
                f"Tool {spec.tool_id!r} is already registered"
            )
        self._specs[spec.tool_id] = spec
        self._handlers[spec.tool_id] = handler

    def unregister(self, tool_id: str, *, caller_is_agent: bool = False) -> bool:
        if caller_is_agent:
            raise ToolRegistrationError(
                "Agents are not permitted to unregister tools"
            )
        if tool_id not in self._specs:
            return False
        del self._specs[tool_id]
        del self._handlers[tool_id]
        return True

    def get_tool(self, tool_id: str) -> ToolSpec:
        spec = self._specs.get(tool_id)
        if spec is None:
            raise ToolExecutionError(f"Requested tool {tool_id!r} is unknown")
        return spec

    def get_handler(self, tool_id: str) -> ToolHandler:
        handler = self._handlers.get(tool_id)
        if handler is None:
            raise ToolExecutionError(f"Requested tool {tool_id!r} is unknown")
        return handler

    def list_tools(self) -> tuple[ToolSpec, ...]:
        return tuple(self._specs.values())

    def get_capabilities(self, tool_id: str) -> frozenset[ToolCapability]:
        return self.get_tool(tool_id).capabilities

    def validate_call(self, call: ToolCall) -> ToolSpec:
        """Validate tool registration, version, parameters, and argument types."""
        spec = self.get_tool(call.tool_id)
        if call.version is not None and call.version != spec.version:
            raise ToolValidationError(
                f"Tool {call.tool_id!r} version mismatch: expected {spec.version!r}, got {call.version!r}"
            )
        if not isinstance(call.arguments, dict):
            raise ToolValidationError("Tool arguments must be a dict")

        allowed_keys = set(spec.input_schema.keys())
        undeclared = set(call.arguments.keys()) - allowed_keys
        if undeclared:
            raise ToolValidationError(
                f"Tool {call.tool_id!r} received undeclared parameters: {sorted(undeclared)}"
            )

        required_keys = allowed_keys - set(spec.optional_params)
        missing = required_keys - set(call.arguments.keys())
        if missing:
            raise ToolValidationError(
                f"Tool {call.tool_id!r} missing required parameters: {sorted(missing)}"
            )

        for key, expected_type in spec.input_schema.items():
            if key not in call.arguments:
                continue
            val = call.arguments[key]
            if not _matches_type(val, expected_type):
                raise ToolValidationError(
                    f"Tool {call.tool_id!r} parameter {key!r} has invalid type {type(val).__name__}"
                )
        return spec

    def validate_output(
        self, spec: ToolSpec, output: dict[str, Any]
    ) -> dict[str, Any]:
        """Validate tool output contract and enforce max_output_bytes."""
        if not isinstance(output, dict):
            raise ToolValidationError(
                f"Tool {spec.tool_id!r} output must be a dict"
            )
        for key, expected_type in spec.output_schema.items():
            if key not in output:
                raise ToolValidationError(
                    f"Tool {spec.tool_id!r} output missing key {key!r}"
                )
            if not _matches_type(output[key], expected_type):
                raise ToolValidationError(
                    f"Tool {spec.tool_id!r} output key {key!r} has invalid type"
                )
        encoded = json.dumps(output, ensure_ascii=False).encode("utf-8")
        if len(encoded) > spec.max_output_bytes:
            raise ToolOutputLimitError(
                f"Tool {spec.tool_id!r} output ({len(encoded)} bytes) exceeds limit ({spec.max_output_bytes} bytes)"
            )
        return output
