"""Tool module loader with hot-reload support.

The ToolLoader discovers, loads, and manages tool modules from the tools/ directory.
It supports hot-reloading of modules when files are modified.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import time
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ._registry import ToolRegistry


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

    def __init__(self, tools_dir: Path, registry: ToolRegistry) -> None:
        """Initialize the loader.

        Args:
            tools_dir: Path to the tools/ directory
            registry: ToolRegistry instance to register tools with
        """
        self.tools_dir = tools_dir
        self.registry = registry
        self._modules: dict[str, ModuleType] = {}
        self._module_versions: dict[str, str] = {}
        self._module_mtimes: dict[str, float] = {}
        self._observer: Any = None
        self._watching = False
        self._reload_callbacks: list[callable] = []

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
            print(f"[tools] Module not found: {module_path}")
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
            spec = importlib.util.spec_from_file_location(
                f"tools.{module_name}", module_path
            )
            if spec is None or spec.loader is None:
                print(f"[tools] Failed to load spec for {module_name}")
                return False

            module = importlib.util.module_from_spec(spec)
            sys.modules[f"tools.{module_name}"] = module
            spec.loader.exec_module(module)

            # Validate module interface
            if not hasattr(module, "TOOLS"):
                print(f"[tools] Module {module_name} missing TOOLS export")
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

            # Register tools
            tools = module.TOOLS
            for tool_def in tools:
                self.registry.register(tool_def, source_module=mod_name)

            # Store module reference
            self._modules[module_name] = module
            self._module_versions[module_name] = mod_version
            self._module_mtimes[module_name] = mtime

            tool_names = [t.name for t in tools]
            print(f"[tools] Loaded {mod_name} v{mod_version}: {tool_names}")
            return True

        except Exception as e:
            print(f"[tools] Error loading {module_name}: {e}")
            import traceback

            traceback.print_exc()
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
                print(f"[tools] Error during cleanup of {module_name}: {e}")

        # Unregister tools
        mod_name = getattr(module, "MODULE_NAME", module_name)
        removed = self.registry.unregister_module(mod_name)
        if removed:
            print(f"[tools] Unregistered tools from {mod_name}: {removed}")

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
                print(f"[tools] Reload callback error: {e}")

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
            print("[tools] watchdog not installed, hot-reload disabled")
            print("[tools] Install with: pip install watchdog")
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

                print(f"[tools] Detected change in {module_name}, reloading...")
                asyncio.create_task(handler_self.loader.reload_module(module_name))

            def on_created(handler_self, event):
                # Treat new files same as modifications
                handler_self.on_modified(event)

        handler = ToolFileHandler(self)
        self._observer = Observer()
        self._observer.schedule(handler, str(self.tools_dir), recursive=False)
        self._observer.start()
        self._watching = True
        print(f"[tools] Watching {self.tools_dir} for changes")
        return True

    def stop_watching(self) -> None:
        """Stop watching for file changes."""
        if self._observer:
            self._observer.stop()
            self._observer.join()
            self._observer = None
        self._watching = False
        print("[tools] Stopped watching for changes")

    def is_watching(self) -> bool:
        """Check if hot-reload watching is active."""
        return self._watching

    def get_loaded_modules(self) -> dict[str, str]:
        """Get dict of loaded module names to versions."""
        return {
            name: self._module_versions.get(name, "unknown")
            for name in self._modules.keys()
        }

    async def shutdown(self) -> None:
        """Shutdown the loader, cleaning up all modules."""
        self.stop_watching()

        # Cleanup all modules
        for module_name in list(self._modules.keys()):
            await self._cleanup_module(module_name)
