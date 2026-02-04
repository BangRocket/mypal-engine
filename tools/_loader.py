"""Tool module loader with hot-reload support.

The ToolLoader discovers, loads, and manages tool modules from the tools/ directory.
It supports hot-reloading of modules when files are modified.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import os
import sys
import time
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ._registry import ToolRegistry

logger = logging.getLogger("tools.loader")


class ToolLoader:
    """Discovers, loads, and hot-reloads tool modules.

    Tool modules are Python files in the tools/ directory that don't start
    with an underscore. Each module must export:
    - MODULE_NAME: str - Unique identifier
    - MODULE_VERSION: str - Version for reload detection
    - TOOLS: list[ToolDef] - Tool definitions

    Optional exports:
    - initialize() -> None - Called after loading
    - cleanup() -> None - Called before unloading

    Usage:
        loader = ToolLoader(tools_dir, registry)
        await loader.load_all()
        loader.start_watching()  # Enable hot-reload
    """

    def __init__(
        self,
        tools_dir: Path,
        registry: ToolRegistry,
        skip_modules: list[str] | None = None,
    ) -> None:
        """Initialize the loader.

        Args:
            tools_dir: Path to the tools/ directory
            registry: ToolRegistry instance to register tools with
            skip_modules: List of module names to skip loading (e.g., replaced by MCP)
        """
        self.tools_dir = tools_dir
        self.registry = registry
        self.skip_modules = set(skip_modules or [])
        self._modules: dict[str, ModuleType] = {}
        self._module_versions: dict[str, str] = {}
        self._module_mtimes: dict[str, float] = {}
        self._observer: Any = None
        self._watching = False
        self._reload_callbacks: list[callable] = []
        self._display_config: dict[str, dict[str, Any]] = self._load_display_config()

    def _load_display_config(self) -> dict[str, dict[str, Any]]:
        """Load tool display configuration from tool_display.json.

        Returns:
            Dict mapping tool names to display metadata
        """
        display_path = self.tools_dir / "tool_display.json"
        if display_path.exists():
            try:
                with display_path.open() as f:
                    config = json.load(f)
                    # Remove comments/meta keys
                    return {k: v for k, v in config.items() if not k.startswith("_")}
            except Exception as e:
                logger.warning(f"Failed to load tool_display.json: {e}")
        return {}

    def _apply_display_metadata(self, tool: Any) -> None:
        """Apply display metadata from config to a tool.

        Args:
            tool: ToolDef to update with display metadata
        """
        config = self._display_config.get(tool.name, {})
        if not config:
            return

        # Apply display fields if present in config
        if "emoji" in config:
            tool.emoji = config["emoji"]
        if "label" in config:
            tool.label = config["label"]
        if "detail_keys" in config:
            tool.detail_keys = config["detail_keys"]
        if "risk_level" in config:
            tool.risk_level = config["risk_level"]
        if "intent" in config:
            tool.intent = config["intent"]

    def discover_modules(self) -> list[str]:
        """Find all tool module files.

        Returns:
            List of module names (without .py extension)
        """
        modules = []
        for f in self.tools_dir.glob("*.py"):
            # Skip private modules (starting with _)
            if f.name.startswith("_"):
                continue
            # Skip __pycache__ etc
            if f.name.startswith("__"):
                continue
            # Skip modules replaced by MCP servers
            if f.stem in self.skip_modules:
                logger.debug(f"Skipping {f.stem} (replaced by MCP server)")
                continue
            modules.append(f.stem)
        return sorted(modules)

    async def load_module(self, module_name: str) -> bool:
        """Load or reload a single tool module.

        Args:
            module_name: Name of the module to load (without .py)

        Returns:
            True if the module was loaded successfully
        """
        module_path = self.tools_dir / f"{module_name}.py"
        if not module_path.exists():
            logger.warning(f"Module not found: {module_path}")
            return False

        # Get file modification time
        mtime = module_path.stat().st_mtime

        # Check if we need to reload
        if module_name in self._modules:
            old_mtime = self._module_mtimes.get(module_name, 0)
            if mtime <= old_mtime:
                # File hasn't changed
                return True

            # Cleanup old module
            await self._cleanup_module(module_name)

        try:
            # Load the module
            spec = importlib.util.spec_from_file_location(f"tools.{module_name}", module_path)
            if spec is None or spec.loader is None:
                logger.warning(f"Failed to load spec for {module_name}")
                return False

            module = importlib.util.module_from_spec(spec)
            sys.modules[f"tools.{module_name}"] = module
            spec.loader.exec_module(module)

            # Validate module interface
            if not hasattr(module, "TOOLS"):
                logger.warning(f"Module {module_name} missing TOOLS export")
                return False

            # Get module metadata
            mod_name = getattr(module, "MODULE_NAME", module_name)
            mod_version = getattr(module, "MODULE_VERSION", "0.0.0")

            # Initialize if needed
            if hasattr(module, "initialize"):
                init_fn = module.initialize
                if asyncio.iscoroutinefunction(init_fn):
                    await init_fn()
                else:
                    init_fn()

            # Register tools with display metadata
            tools = module.TOOLS
            for tool_def in tools:
                # Apply display metadata from config
                self._apply_display_metadata(tool_def)
                self.registry.register(tool_def, source_module=mod_name)

            # Register system prompt only if module has active tools
            if tools:
                system_prompt = getattr(module, "SYSTEM_PROMPT", None)
                if system_prompt:
                    self.registry.register_system_prompt(mod_name, system_prompt)

            # Store module reference
            self._modules[module_name] = module
            self._module_versions[module_name] = mod_version
            self._module_mtimes[module_name] = mtime

            tool_names = [t.name for t in tools]
            logger.debug(f"Loaded {mod_name} v{mod_version}: {tool_names}")
            return True

        except Exception as e:
            logger.error(f"Error loading {module_name}: {e}", exc_info=True)
            return False

    async def _cleanup_module(self, module_name: str) -> None:
        """Clean up a module before unloading/reloading."""
        module = self._modules.get(module_name)
        if module is None:
            return

        # Call cleanup if defined
        if hasattr(module, "cleanup"):
            cleanup_fn = module.cleanup
            try:
                if asyncio.iscoroutinefunction(cleanup_fn):
                    await cleanup_fn()
                else:
                    cleanup_fn()
            except Exception as e:
                logger.error(f"Error during cleanup of {module_name}: {e}")

        # Unregister tools and system prompt
        mod_name = getattr(module, "MODULE_NAME", module_name)
        removed = self.registry.unregister_module(mod_name)
        if removed:
            logger.debug(f"Unregistered tools from {mod_name}: {removed}")
        self.registry.unregister_system_prompt(mod_name)

        # Remove from sys.modules
        sys_key = f"tools.{module_name}"
        if sys_key in sys.modules:
            del sys.modules[sys_key]

        # Remove from our tracking
        del self._modules[module_name]
        if module_name in self._module_versions:
            del self._module_versions[module_name]
        if module_name in self._module_mtimes:
            del self._module_mtimes[module_name]

    async def unload_module(self, module_name: str) -> bool:
        """Unload a tool module.

        Args:
            module_name: Name of the module to unload

        Returns:
            True if the module was unloaded
        """
        if module_name not in self._modules:
            return False

        await self._cleanup_module(module_name)
        return True

    async def load_all(self) -> dict[str, bool]:
        """Load all discovered tool modules.

        Returns:
            Dict mapping module names to load success status
        """
        results = {}
        for name in self.discover_modules():
            results[name] = await self.load_module(name)
        return results

    async def reload_module(self, module_name: str) -> bool:
        """Reload a specific module.

        Args:
            module_name: Name of the module to reload

        Returns:
            True if reload was successful
        """
        # Force reload by clearing mtime
        if module_name in self._module_mtimes:
            self._module_mtimes[module_name] = 0

        success = await self.load_module(module_name)

        # Notify callbacks
        for callback in self._reload_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(module_name, success)
                else:
                    callback(module_name, success)
            except Exception as e:
                logger.error(f"Reload callback error: {e}")

        return success

    async def reload_all(self) -> dict[str, bool]:
        """Reload all modules.

        Returns:
            Dict mapping module names to reload success status
        """
        # Clear all mtimes to force reload
        self._module_mtimes.clear()
        return await self.load_all()

    def on_reload(self, callback: callable) -> None:
        """Register a callback to be called when modules are reloaded.

        Args:
            callback: Function(module_name: str, success: bool) -> None
        """
        self._reload_callbacks.append(callback)

    def start_watching(self) -> bool:
        """Start watching for file changes (hot-reload).

        Returns:
            True if watching was started successfully
        """
        if self._watching:
            return True

        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer
        except ImportError:
            logger.debug("watchdog not installed, hot-reload disabled")
            return False

        class ToolFileHandler(FileSystemEventHandler):
            def __init__(handler_self, loader: ToolLoader):
                handler_self.loader = loader
                handler_self._debounce: dict[str, float] = {}
                handler_self._debounce_delay = 0.5  # seconds

            def on_modified(handler_self, event):
                if event.is_directory:
                    return

                path = Path(event.src_path)
                if not path.suffix == ".py":
                    return
                if path.name.startswith("_"):
                    return

                module_name = path.stem
                now = time.time()

                # Debounce rapid changes
                last_change = handler_self._debounce.get(module_name, 0)
                if now - last_change < handler_self._debounce_delay:
                    return
                handler_self._debounce[module_name] = now

                logger.info(f"Detected change in {module_name}, reloading...")
                asyncio.create_task(handler_self.loader.reload_module(module_name))

            def on_created(handler_self, event):
                # Treat new files same as modifications
                handler_self.on_modified(event)

        handler = ToolFileHandler(self)
        self._observer = Observer()
        self._observer.schedule(handler, str(self.tools_dir), recursive=False)
        self._observer.start()
        self._watching = True
        logger.debug(f"Watching {self.tools_dir} for changes")
        return True

    def stop_watching(self) -> None:
        """Stop watching for file changes."""
        if self._observer:
            self._observer.stop()
            self._observer.join()
            self._observer = None
        self._watching = False
        logger.debug("Stopped watching for changes")

    def is_watching(self) -> bool:
        """Check if hot-reload watching is active."""
        return self._watching

    def get_loaded_modules(self) -> dict[str, str]:
        """Get dict of loaded module names to versions."""
        return {name: self._module_versions.get(name, "unknown") for name in self._modules.keys()}

    def reload_display_config(self) -> None:
        """Reload the tool display configuration and reapply to all loaded tools."""
        self._display_config = self._load_display_config()
        logger.info(f"Reloaded display config with {len(self._display_config)} tool configs")

    def get_display_config(self, tool_name: str) -> dict[str, Any]:
        """Get display configuration for a tool.

        Args:
            tool_name: Name of the tool

        Returns:
            Display config dict, or empty dict if not found
        """
        return self._display_config.get(tool_name, {})

    async def shutdown(self) -> None:
        """Shutdown the loader, cleaning up all modules."""
        self.stop_watching()

        # Cleanup all modules
        for module_name in list(self._modules.keys()):
            await self._cleanup_module(module_name)
