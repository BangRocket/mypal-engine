"""Schema-validity tests for every obsidian_* tool.

These tests catch malformed tool schemas that individual handler tests miss —
specifically, properties without a `type` field, which some LLM providers
silently reject (Claude via clewdr has been observed to hang on requests
with loose tool schemas).
"""
from __future__ import annotations

from typing import Any

import pytest
from jsonschema import Draft7Validator

from mypalclara.core.core_tools.obsidian_tool import TOOLS
from mypalclara.tools._base import ToolDef


def _obsidian_tools() -> list[ToolDef]:
    return [t for t in TOOLS if t.name.startswith("obsidian_")]


def test_all_sixteen_obsidian_tools_registered():
    """Guardrail: we ship 16 obsidian_* tools. Drift should fail loudly."""
    assert len(_obsidian_tools()) == 16


@pytest.mark.parametrize("tool", _obsidian_tools(), ids=lambda t: t.name)
def test_parameters_is_valid_json_schema(tool: ToolDef):
    """Every tool's parameters must pass Draft-07 schema validation."""
    Draft7Validator.check_schema(tool.parameters)


@pytest.mark.parametrize("tool", _obsidian_tools(), ids=lambda t: t.name)
def test_parameters_has_object_type(tool: ToolDef):
    """Anthropic/OpenAI tool schemas require the top-level to be `type: object`."""
    assert tool.parameters.get("type") == "object", (
        f"{tool.name}: top-level parameters must be type=object"
    )


@pytest.mark.parametrize("tool", _obsidian_tools(), ids=lambda t: t.name)
def test_every_property_has_explicit_type(tool: ToolDef):
    """Every property MUST declare a `type`.

    JSON Schema allows properties without `type` (it means "any"), but
    Anthropic's tool-use validator and clewdr have been observed to reject
    or hang on schemas where a property lacks `type`. Catching it here
    prevents Clara from hanging mid-turn on live traffic.
    """
    props = tool.parameters.get("properties", {})
    missing: list[str] = [
        name for name, schema in props.items() if "type" not in schema
    ]
    assert not missing, (
        f"{tool.name}: properties without explicit `type`: {missing}. "
        "Add an explicit type (e.g. 'string', 'object', 'integer')."
    )


@pytest.mark.parametrize("tool", _obsidian_tools(), ids=lambda t: t.name)
def test_required_entries_all_exist_as_properties(tool: ToolDef):
    """Every name in `required` must appear in `properties`."""
    props = set(tool.parameters.get("properties", {}).keys())
    required = set(tool.parameters.get("required", []))
    missing = required - props
    assert not missing, f"{tool.name}: required refers to missing properties: {missing}"


@pytest.mark.parametrize("tool", _obsidian_tools(), ids=lambda t: t.name)
def test_to_openai_format_roundtrip(tool: ToolDef):
    """Every tool must serialize cleanly to OpenAI tool format."""
    fmt = tool.to_openai_format()
    assert fmt["type"] == "function"
    assert fmt["function"]["name"] == tool.name
    assert fmt["function"]["description"] == tool.description
    assert fmt["function"]["parameters"] == tool.parameters


@pytest.mark.parametrize("tool", _obsidian_tools(), ids=lambda t: t.name)
def test_to_claude_format_roundtrip(tool: ToolDef):
    """Every tool must serialize cleanly to Claude native tool format."""
    fmt = tool.to_claude_format()
    assert fmt["name"] == tool.name
    assert fmt["description"] == tool.description
    assert fmt["input_schema"] == tool.parameters
    # No `type` outer field in Claude native format (it's on input_schema)
    assert "type" not in fmt


@pytest.mark.parametrize("tool", _obsidian_tools(), ids=lambda t: t.name)
def test_no_unknown_top_level_keys(tool: ToolDef):
    """Parameters must only use recognized top-level keys so we don't quietly
    ship something providers might reject."""
    allowed = {"type", "properties", "required", "additionalProperties", "description"}
    extra = set(tool.parameters.keys()) - allowed
    assert not extra, f"{tool.name}: unknown top-level keys: {extra}"


def test_property_descriptions_are_strings_when_present():
    """Every property that has a description must have a STRING description
    (not None, not a dict). Non-string descriptions break serialization."""
    for tool in _obsidian_tools():
        props: dict[str, dict[str, Any]] = tool.parameters.get("properties", {})
        for prop_name, schema in props.items():
            if "description" in schema:
                assert isinstance(schema["description"], str), (
                    f"{tool.name}.{prop_name}: description must be str, "
                    f"got {type(schema['description'])}"
                )
