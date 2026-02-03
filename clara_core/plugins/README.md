# Clara Plugin System

The Clara plugin system provides a unified architecture for extending Clara with tools, hooks, channels, providers, and services. It is inspired by OpenClaw's plugin architecture.

## Quick Start

```python
from clara_core.plugins import initialize_plugins, get_registry

# Initialize the plugin system
registry = await initialize_plugins(
    config=app_config,
    workspace_dir=Path.cwd(),
    enable_mcp=True,
)

# Get all tools
tools = registry.get_tools()

# Emit a hook
await registry.emit_hook("message_received", user_id="123", content="hello")
```

## Architecture

### Core Components

1. **Plugin Types** (`types.py`)
   - `PluginKind`: Plugin type classification (tools, mcp, memory, channel, provider, service)
   - `PluginAPI`: API passed to plugin `register()` function
   - `PluginRuntime`: Core capabilities available to plugins
   - Event dataclasses for all hooks

2. **Manifest System** (`manifest.py`)
   - Loads and validates `clara.plugin.json`
   - Supports multiple manifest filenames
   - JSON Schema validation for plugin config

3. **Plugin Registry** (`registry.py`)
   - Central registry for all plugins
   - Manages tools, hooks, channels, providers
   - Handles factory-based tool resolution

4. **Plugin Loader** (`loader.py`)
   - Discovers plugins from multiple locations
   - Loads plugin modules and calls `register()`
   - Handles errors and diagnostics

5. **Plugin Runtime** (`runtime.py`)
   - Path resolution
   - State management
   - Command execution
   - Hook registration

6. **Hook System** (`hooks.py`)
   - 21 hook event types defined
   - Event dataclasses for each hook
   - Type-safe handler registration

7. **MCP Integration** (`mcp_integration.py`)
   - Adapts existing MCP system as a plugin
   - Registers MCP tools as plugin tools
   - Emits MCP lifecycle hooks

8. **Compatibility Layer** (`compat.py`)
   - `ToolRegistryAdapter` maintains old `ToolRegistry` interface
   - Bridges new plugin system to existing code
   - Non-breaking migration path

## Creating a Plugin

### 1. Plugin Directory Structure

```
my_plugin/
├── plugin.py              # Plugin code
├── clara.plugin.json       # Plugin manifest (required)
└── README.md              # Plugin documentation (optional)
```

### 2. Plugin Manifest (`clara.plugin.json`)

```json
{
  "id": "my-plugin",
  "name": "My Plugin",
  "version": "1.0.0",
  "description": "A plugin that does something cool",
  "kind": "tools",
  "configSchema": {
    "type": "object",
    "properties": {
      "enabled": {
        "type": "boolean",
        "default": true,
        "description": "Whether to enable this plugin"
      },
      "apiKey": {
        "type": "string",
        "description": "API key for external service"
      }
    },
    "required": []
  },
  "uiHints": {
    "apiKey": {
      "label": "API Key",
      "sensitive": true,
      "help": "Enter your API key here"
    }
  },
  "tools": ["my_tool"],
  "hooks": ["message_received"]
}
```

### 3. Plugin Code (`plugin.py`)

```python
from clara_core.plugins import PluginAPI, PluginContext
from clara_core.plugins._base import ToolDef, ToolContext


def register(api: PluginAPI):
    """Plugin entry point - called when plugin is loaded.

    Args:
        api: PluginAPI for registering tools, hooks, etc.
    """
    # Log initialization
    api.info(f"Registering plugin: {api.id}")

    # Register a tool
    api.register_tool(lambda ctx: [
        ToolDef(
            name="my_tool",
            description="Does something useful",
            parameters={
                "type": "object",
                "properties": {
                    "input": {
                        "type": "string",
                        "description": "Input parameter"
                    }
                },
                "required": ["input"],
            },
            handler=_make_tool_handler(api),
        )
    ])

    # Register a hook
    api.register_hook("message_received", _on_message_received)


def _make_tool_handler(api: PluginAPI):
    """Create a tool handler that uses the plugin API.

    Args:
        api: PluginAPI instance

    Returns:
        Async tool handler function
    """
    async def handler(args: dict, ctx: ToolContext) -> str:
        input_value = args.get("input", "")

        # Use plugin config
        api_key = api.plugin_config.get("apiKey")

        # Use runtime capabilities
        file_path = api.runtime.resolve_path("data.txt")

        # Log the action
        api.info(f"Executing tool for user {ctx.user_id}")

        # Do the work
        result = f"Processed: {input_value}"

        return result

    return handler


async def _on_message_received(event) -> None:
    """Hook handler for message_received event.

    Args:
        event: MessageReceivedEvent
    """
    print(f"Message received from {event.user_id}: {event.content}")
```

### 4. Plugin Installation

Plugins are discovered from these locations (in order):

1. **Bundled**: `<codebase>/clara_core/plugins/bundled/`
2. **Global**: `~/.mypalclara/plugins/`
3. **Workspace**: `<workspace>/.mypalclara/plugins/`
4. **Extra**: From `config.plugin_paths` in main config

To install a plugin:
- Create plugin directory in one of the above locations
- Add `plugin.py` with `register()` function
- Add `clara.plugin.json` with plugin metadata
- Restart Clara (or use reload if supported)

## Available Hooks

### Message Hooks

- `message_received`: Before Clara processes an incoming message
- `message_sending`: Before Clara sends a response (can modify/cancel)
- `message_sent`: After Clara sends a response

### Tool Hooks

- `tool_start`: Before a tool is executed
- `tool_end`: After a tool completes (with result)
- `tool_error`: When a tool throws an error

### Session Hooks

- `session_start`: When a user session begins
- `session_end`: When a user session ends
- `session_timeout`: When a session times out

### LLM Hooks

- `llm_request`: Before calling the LLM
- `llm_response`: After receiving LLM response
- `llm_error`: When LLM call fails

### Memory Hooks

- `memory_read`: When reading from memory
- `memory_write`: When writing to memory

### MCP Hooks

- `mcp_server_start`: When an MCP server starts
- `mcp_server_stop`: When an MCP server stops
- `mcp_server_error`: When an MCP server encounters an error

## Plugin Configuration

Plugins can define their configuration schema in `clara.plugin.json`:

```json
{
  "configSchema": {
    "type": "object",
    "properties": {
      "mySetting": {
        "type": "string",
        "default": "default value",
        "description": "What this setting does"
      }
    }
  }
}
```

Access plugin config in your plugin:

```python
def register(api: PluginAPI):
    # Get plugin-specific config
    my_setting = api.plugin_config.get("mySetting", "default value")

    # Get main app config
    app_setting = api.config.get("someGlobalSetting")
```

## Runtime Capabilities

Plugins have access to these runtime capabilities via `api.runtime`:

```python
def register(api: PluginAPI):
    # Resolve paths relative to config dir
    data_file = api.runtime.resolve_path("data.txt")

    # State management (simple key-value store)
    api.runtime.set_state("my_key", "my_value")
    value = api.runtime.get_state("my_key")

    # Run shell commands
    returncode, stdout, stderr = await api.runtime.run_command("ls -la")
```

## Tool Factory Pattern

Plugins can register tool factories that create different tools based on context:

```python
def register(api: PluginAPI):
    def tool_factory(ctx: PluginContext):
        # Create different tools based on context
        if ctx.platform == "discord":
            return [
                ToolDef(name="discord_tool", ...),
                ToolDef(name="discord_special", ...),
            ]
        else:
            return ToolDef(name="generic_tool", ...)

    # Register the factory
    api.register_tool(tool_factory)
```

## Migration from Old Tool System

Existing tool modules that export `TOOLS` lists continue to work via the compatibility layer:

```python
# Old-style tool (still works)
from clara_core.tools._base import ToolDef

TOOLS = [
    ToolDef(name="old_tool", ...),
]
```

To migrate to the new plugin system:

1. Create `clara.plugin.json` manifest
2. Change from `TOOLS = [...]` to `def register(api): ...`
3. Use `api.register_tool()` instead of global list
4. Use `api.plugin_config` for configuration
5. Use `api.runtime` for system capabilities

## MCP Integration

The MCP system is integrated as a plugin. All MCP tools are automatically registered with namespaced names like `server__tool`.

To use MCP tools, just install MCP servers using the existing tools:
- MCP servers are discovered as plugins
- Tools are registered automatically
- Hooks are emitted for MCP lifecycle events

## Debugging

Enable debug logging:

```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

Check plugin status:

```python
from clara_core.plugins import get_plugin_status

status = get_plugin_status()
for plugin_id, info in status.items():
    print(f"{plugin_id}: {info['status']} ({info['tools']} tools)")
```

## Testing

Test your plugin by:

1. Create plugin in one of the discovery locations
2. Run Clara with debug logging
3. Check for errors in plugin loading
4. Verify tools appear in tool list
5. Test tool execution
6. Test hook registration and emission

## Examples

See `clara_core/plugins/examples/` for complete example plugins.

## API Reference

### PluginAPI

```python
@dataclass
class PluginAPI:
    # Plugin metadata
    id: str
    name: str
    version: str | None
    description: str | None
    source: str

    # Configuration
    config: dict[str, Any]  # Main app config
    plugin_config: dict[str, Any]  # Plugin-specific config

    # Runtime
    runtime: PluginRuntime

    # Logging
    logger: logging.Logger

    # Registration methods
    register_tool(tool) -> None
    register_hook(event, handler) -> None
    register_channel(plugin) -> None
    register_provider(plugin) -> None
    register_service(service) -> None
    register_command(command) -> None

    # Utility methods
    resolve_path(path) -> Path

    # Logging helpers
    debug(msg, *args) -> None
    info(msg, *args) -> None
    warn(msg, *args) -> None
    error(msg, *args) -> None
```

### System Functions

```python
# Initialize plugin system
registry = await initialize_plugins(config, workspace_dir, enable_mcp)

# Get registry
registry = get_registry()

# Reload all plugins
registry = await reload_plugins(config, workspace_dir)

# Get plugin status
status = get_plugin_status()

# Emit hooks
results = await emit_system_hooks("message_received", user_id="123")

# Shutdown
await shutdown_plugins()
```

## Best Practices

1. **Use manifest for configuration**: Define config schema, don't require manual config
2. **Handle errors gracefully**: Use try/except and log errors via `api.error()`
3. **Use context-aware factories**: Create tools based on `PluginContext` when needed
4. **Register hooks**: Respond to lifecycle events for better integration
5. **Use namespacing**: Avoid name conflicts with existing tools
6. **Test thoroughly**: Test with various contexts and configurations
7. **Document**: Include README in plugin directory
8. **Version properly**: Use semantic versioning in manifest

## Troubleshooting

**Plugin not loading?**
- Check manifest is valid JSON
- Verify `register()` function exists
- Check for import errors in logs
- Ensure plugin is in discovery location

**Tools not appearing?**
- Verify `api.register_tool()` was called
- Check for tool name conflicts
- Ensure factory returns valid ToolDef objects

**Hooks not firing?**
- Verify hook registration with correct event name
- Check if hooks are being emitted in core code
- Test hook handler with event data structure

## Support

For questions or issues:
- Check the example plugins in `clara_core/plugins/examples/`
- Review the OpenClaw plugin system for inspiration
- Examine existing Clara tools for patterns
