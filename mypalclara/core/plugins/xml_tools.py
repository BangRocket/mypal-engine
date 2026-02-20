"""OpenClaw-style XML tool serialization and parsing.

This module provides XML-based tool communication instead of using
native API tool calling. Tools are injected into the system prompt
as XML, and function calls are parsed from the LLM response.

This approach:
- Works with any LLM provider (doesn't require native tool support)
- Gives the LLM full context about available tools in the prompt
- Uses a consistent format regardless of provider
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mypalclara.tools._base import ToolDef

logger = logging.getLogger(__name__)


@dataclass
class ParsedFunctionCall:
    """A parsed function call from LLM response."""

    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    raw_xml: str = ""


def _build_functions_header() -> str:
    """Build the functions block header with invocation instructions."""
    # Using chr() to avoid XML-like tags being interpreted
    lt = "<"
    gt = ">"
    return f"""{lt}functions{gt}
You have access to a set of tools you can use to answer the user's question.
You can invoke functions by writing a "{lt}function_calls{gt}" block like the following as part of your reply to the user:

{lt}function_calls{gt}
{lt}invoke name="$FUNCTION_NAME"{gt}
{lt}parameter name="$PARAMETER_NAME"{gt}$PARAMETER_VALUE{lt}/parameter{gt}
...
{lt}/invoke{gt}
{lt}invoke name="$FUNCTION_NAME2"{gt}
...
{lt}/invoke{gt}
{lt}/function_calls{gt}

String and scalar parameters should be specified as is, while lists and objects should use JSON format.

Here are the functions available:
"""


def _build_functions_footer() -> str:
    """Build the functions block footer."""
    return "</functions>"


def tool_to_xml(tool: "ToolDef") -> str:
    """Convert a tool definition to XML format.

    Args:
        tool: ToolDef to convert

    Returns:
        XML string representation of the tool
    """
    # Build JSON schema description
    params = tool.parameters
    params_json = json.dumps(params, indent=2)

    return f"""<function>
<name>{tool.name}</name>
<description>{_escape_xml(tool.description)}</description>
<parameters>{params_json}</parameters>
</function>"""


def tools_to_xml(tools: list["ToolDef"]) -> str:
    """Convert a list of tools to the full XML functions block.

    Args:
        tools: List of ToolDef objects

    Returns:
        Complete XML functions block for system prompt injection
    """
    if not tools:
        return ""

    parts = [_build_functions_header()]

    for tool in tools:
        parts.append(tool_to_xml(tool))

    parts.append(_build_functions_footer())

    return "\n".join(parts)


def tools_to_xml_from_dicts(tools: list[dict[str, Any]]) -> str:
    """Convert tool definitions (in OpenAI format) to XML.

    Args:
        tools: List of tool dicts in OpenAI format

    Returns:
        Complete XML functions block
    """
    if not tools:
        return ""

    parts = [_build_functions_header()]

    for tool in tools:
        if tool.get("type") == "function" and "function" in tool:
            func = tool["function"]
            name = func.get("name", "unknown")
            desc = func.get("description", "")
            params = func.get("parameters", {"type": "object", "properties": {}})
            params_json = json.dumps(params, indent=2)

            parts.append(f"""<function>
<name>{name}</name>
<description>{_escape_xml(desc)}</description>
<parameters>{params_json}</parameters>
</function>""")

    parts.append(_build_functions_footer())

    return "\n".join(parts)


def _escape_xml(text: str) -> str:
    """Escape special XML characters.

    Args:
        text: Text to escape

    Returns:
        Escaped text safe for XML
    """
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _unescape_xml(text: str) -> str:
    """Unescape XML entities.

    Args:
        text: Text with XML entities

    Returns:
        Unescaped text
    """
    return (
        text.replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&apos;", "'")
        .replace("&amp;", "&")
    )


def parse_function_calls(response: str) -> list[ParsedFunctionCall]:
    """Parse function calls from LLM response text.

    Looks for function_calls blocks in the response and extracts
    the function names and parameters.

    Supports multiple formats:
    1. OpenClaw format: <function_calls><invoke name="...">...</invoke></function_calls>
    2. GLM format: <tool_call>name<arg_key>key</arg_key><arg_value>value</arg_value></tool_call>

    Args:
        response: Full LLM response text

    Returns:
        List of parsed function calls
    """
    calls: list[ParsedFunctionCall] = []

    # First, try OpenClaw format: <function_calls>...</function_calls>
    block_pattern = re.compile(
        r"<function_calls>(.*?)</function_calls>",
        re.DOTALL | re.IGNORECASE,
    )

    for block_match in block_pattern.finditer(response):
        block_content = block_match.group(1)

        # Find all invoke blocks within this function_calls block
        invoke_pattern = re.compile(
            r'<invoke\s+name=["\']([^"\']+)["\']>(.*?)</invoke>',
            re.DOTALL | re.IGNORECASE,
        )

        for invoke_match in invoke_pattern.finditer(block_content):
            func_name = invoke_match.group(1)
            invoke_content = invoke_match.group(2)
            raw_xml = invoke_match.group(0)

            # Parse parameters
            arguments = _parse_parameters(invoke_content)

            calls.append(
                ParsedFunctionCall(
                    name=func_name,
                    arguments=arguments,
                    raw_xml=raw_xml,
                )
            )

    # Also try GLM format: <tool_call>name<arg_key>key</arg_key><arg_value>value</arg_value></tool_call>
    glm_calls = _parse_glm_tool_calls(response)
    calls.extend(glm_calls)

    return calls


def _parse_glm_tool_calls(response: str) -> list[ParsedFunctionCall]:
    """Parse GLM-style tool calls from response.

    GLM format: <tool_call>func_name<arg_key>key</arg_key><arg_value>value</arg_value>...</tool_call>

    Args:
        response: Full LLM response text

    Returns:
        List of parsed function calls
    """
    calls: list[ParsedFunctionCall] = []

    # Pattern for <tool_call>...</tool_call>
    tool_call_pattern = re.compile(
        r"<tool_call>(.*?)</tool_call>",
        re.DOTALL | re.IGNORECASE,
    )

    for match in tool_call_pattern.finditer(response):
        content = match.group(1).strip()
        raw_xml = match.group(0)

        # Extract function name (text before first <arg_key>)
        arg_key_idx = content.lower().find("<arg_key>")
        if arg_key_idx == -1:
            # No arguments, just function name
            func_name = content.strip()
            arguments: dict[str, Any] = {}
        else:
            func_name = content[:arg_key_idx].strip()
            arguments = _parse_glm_arguments(content[arg_key_idx:])

        if func_name:
            calls.append(
                ParsedFunctionCall(
                    name=func_name,
                    arguments=arguments,
                    raw_xml=raw_xml,
                )
            )

    return calls


def _parse_glm_arguments(content: str) -> dict[str, Any]:
    """Parse GLM-style arguments.

    Format: <arg_key>key</arg_key><arg_value>value</arg_value>...

    Args:
        content: Content containing arg_key/arg_value pairs

    Returns:
        Dict of argument name -> value
    """
    args: dict[str, Any] = {}

    # Find all arg_key/arg_value pairs
    # Pattern matches pairs of <arg_key>name</arg_key><arg_value>value</arg_value>
    pair_pattern = re.compile(
        r"<arg_key>(.*?)</arg_key>\s*<arg_value>(.*?)</arg_value>",
        re.DOTALL | re.IGNORECASE,
    )

    for pair_match in pair_pattern.finditer(content):
        key = pair_match.group(1).strip()
        value = pair_match.group(2).strip()

        if key:
            parsed_value = _parse_parameter_value(value)
            args[key] = parsed_value

    return args


def _parse_parameters(invoke_content: str) -> dict[str, Any]:
    """Parse parameter tags from invoke content.

    Args:
        invoke_content: Content inside an invoke tag

    Returns:
        Dict of parameter name -> value
    """
    params: dict[str, Any] = {}

    # Pattern for <parameter name="...">value</parameter>
    param_pattern = re.compile(
        r'<parameter\s+name=["\']([^"\']+)["\']>(.*?)</parameter>',
        re.DOTALL | re.IGNORECASE,
    )

    for match in param_pattern.finditer(invoke_content):
        name = match.group(1)
        value = match.group(2).strip()

        # Try to parse as JSON (for objects/arrays)
        parsed_value = _parse_parameter_value(value)
        params[name] = parsed_value

    return params


def _parse_parameter_value(value: str) -> Any:
    """Parse a parameter value, attempting JSON decode for complex types.

    Args:
        value: Raw parameter value string

    Returns:
        Parsed value (dict, list, or string)
    """
    # Unescape XML entities first
    value = _unescape_xml(value)

    # Try JSON parse for objects/arrays
    if value.startswith(("{", "[")):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            pass

    # Try to parse as boolean
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False

    # Try to parse as number
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        pass

    # Return as string
    return value


def has_function_calls(response: str) -> bool:
    """Check if response contains function calls.

    Supports both OpenClaw format (<function_calls>) and GLM format (<tool_call>).

    Args:
        response: LLM response text

    Returns:
        True if response contains function_calls or tool_call block
    """
    lower_response = response.lower()
    return "<function_calls>" in lower_response or "<tool_call>" in lower_response


def extract_text_before_function_calls(response: str) -> str:
    """Extract text content before the first function_calls or tool_call block.

    Args:
        response: Full LLM response

    Returns:
        Text before function calls (empty string if none)
    """
    # Case-insensitive search for both formats
    lower_response = response.lower()

    # Find earliest occurrence of either format
    openclaw_idx = lower_response.find("<function_calls>")
    glm_idx = lower_response.find("<tool_call>")

    # Get the earliest index
    indices = [i for i in [openclaw_idx, glm_idx] if i != -1]
    if not indices:
        return response

    idx = min(indices)
    return response[:idx].strip()


def extract_text_after_function_calls(response: str) -> str:
    """Extract text content after the last function_calls or tool_call block.

    Args:
        response: Full LLM response

    Returns:
        Text after function calls (empty string if none)
    """
    # Find the last closing tag of either format
    lower_response = response.lower()

    openclaw_idx = lower_response.rfind("</function_calls>")
    glm_idx = lower_response.rfind("</tool_call>")

    # Get the latest index and corresponding tag length
    if openclaw_idx == -1 and glm_idx == -1:
        return ""

    if openclaw_idx > glm_idx:
        end_idx = openclaw_idx + len("</function_calls>")
    else:
        end_idx = glm_idx + len("</tool_call>")

    return response[end_idx:].strip()


def format_function_result(
    func_name: str,
    result: str,
    is_error: bool = False,
) -> str:
    """Format a function result for injection back into conversation.

    Args:
        func_name: Name of the function that was called
        result: Result string from the function
        is_error: Whether this is an error result

    Returns:
        Formatted result block
    """
    if is_error:
        return f"""<function_results>
<result>
<name>{func_name}</name>
<error>{_escape_xml(result)}</error>
</result>
</function_results>"""
    else:
        return f"""<function_results>
<result>
<name>{func_name}</name>
<output>{_escape_xml(result)}</output>
</result>
</function_results>"""


def format_multiple_function_results(
    results: list[tuple[str, str, bool]],
) -> str:
    """Format multiple function results into a single block.

    Args:
        results: List of (func_name, result, is_error) tuples

    Returns:
        Formatted results block
    """
    parts = ["<function_results>"]

    for func_name, result, is_error in results:
        if is_error:
            parts.append(f"""<result>
<name>{func_name}</name>
<error>{_escape_xml(result)}</error>
</result>""")
        else:
            parts.append(f"""<result>
<name>{func_name}</name>
<output>{_escape_xml(result)}</output>
</result>""")

    parts.append("</function_results>")

    return "\n".join(parts)


def convert_to_openai_tool_calls(
    parsed_calls: list[ParsedFunctionCall],
) -> list[dict[str, Any]]:
    """Convert parsed function calls to OpenAI tool_calls format.

    This allows existing code that expects OpenAI format to work
    with XML-parsed function calls.

    Args:
        parsed_calls: List of ParsedFunctionCall objects

    Returns:
        List of tool calls in OpenAI format
    """
    tool_calls = []

    for i, call in enumerate(parsed_calls):
        tool_calls.append(
            {
                "id": f"call_{i}",
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments),
                },
            }
        )

    return tool_calls
