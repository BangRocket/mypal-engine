# Tool Format Standardization Plan (Issue #112)

## Investigation Summary

After auditing the codebase, the tool system is well-architected with proper format conversion:

- `ToolDef` class has `to_openai_format()`, `to_claude_format()`, and `to_mcp_format()` methods
- `anthropic_to_openai_response()` properly converts Anthropic responses to OpenAI format
- JSON serialization/deserialization is consistent across providers
- Audited 81 tools - no JSON Schema issues found

## Issues Identified

### 1. GitHub Projects Parameter Naming (Minor)
- `project_id` can accept either node IDs (PVT_xxx) or project numbers
- This is flexible by design but the description could be clearer
- **Status**: Working as designed, documentation improvement only

### 2. Nested Object Parameters
- Some tools use `"type": "object"` for nested parameters (e.g., `github_update_project_item_field.value`)
- These serialize correctly but error handling for malformed input could be improved

### 3. Array Parameter Edge Cases
- All array parameters have proper `items` schema defined
- No issues found during audit

## Recommended Changes

### Phase 1: Parameter Validation (This PR)

Add runtime validation helper for tool arguments:

```python
def validate_tool_args(args: dict, parameters: dict) -> tuple[dict, list[str]]:
    """Validate and coerce tool arguments against schema.

    Returns:
        (validated_args, warnings) - args with type coercion, list of any issues
    """
```

Benefits:
- Catch malformed arguments early
- Provide helpful error messages
- Handle string-to-array coercion (when LLM passes `"item"` instead of `["item"]`)

### Phase 2: Documentation Improvements

1. Add docstrings explaining parameter flexibility (e.g., `project_id` accepting both formats)
2. Add examples to tool descriptions for complex parameters

### Phase 3: Test Coverage

1. Add unit tests for tool format conversion
2. Add integration tests for tool execution with edge-case inputs
3. Test with both OpenAI and Anthropic providers

## Files to Modify

| File | Change |
|------|--------|
| `tools/_registry.py` | Add `validate_tool_args()` helper |
| `tools/github.py` | Improve `project_id` parameter description |
| `discord_bot.py` | Call validation before tool execution |

## Implementation

### 1. Add validation helper to _registry.py

```python
def validate_tool_args(
    tool_name: str,
    args: dict,
    parameters: dict
) -> tuple[dict, list[str]]:
    """Validate tool arguments against JSON schema with helpful coercion."""
    validated = {}
    warnings = []

    props = parameters.get("properties", {})
    required = set(parameters.get("required", []))

    # Check required params
    for req in required:
        if req not in args:
            warnings.append(f"Missing required parameter: {req}")

    # Validate and coerce each argument
    for name, value in args.items():
        if name not in props:
            warnings.append(f"Unknown parameter: {name}")
            validated[name] = value
            continue

        prop_def = props[name]
        expected_type = prop_def.get("type")

        # Type coercion
        if expected_type == "array" and isinstance(value, str):
            # Try to parse as JSON, or wrap single value
            try:
                import json
                parsed = json.loads(value)
                if isinstance(parsed, list):
                    validated[name] = parsed
                else:
                    validated[name] = [value]
            except:
                validated[name] = [value]
            warnings.append(f"Coerced string to array for {name}")
        elif expected_type == "integer" and isinstance(value, str):
            try:
                validated[name] = int(value)
            except ValueError:
                validated[name] = value
                warnings.append(f"Failed to coerce {name} to integer")
        elif expected_type == "boolean" and isinstance(value, str):
            validated[name] = value.lower() in ("true", "1", "yes")
        else:
            validated[name] = value

    return validated, warnings
```

### 2. Improve GitHub project_id description

```python
"project_id": {
    "type": "string",
    "description": (
        "Project identifier. Accepts either:\n"
        "- Node ID (e.g., 'PVT_kwHOBxxx') from github_list_projects\n"
        "- Project number (e.g., '1') when used with owner/type params"
    ),
},
```

### 3. Add validation in discord_bot.py

```python
# After parsing arguments
arguments = json.loads(raw_args) if raw_args else {}

# Validate arguments
tool_def = registry.get_tool(tool_name)
if tool_def:
    arguments, warnings = validate_tool_args(
        tool_name,
        arguments,
        tool_def.parameters
    )
    for w in warnings:
        tools_logger.warning(f"[{tool_name}] {w}")
```
