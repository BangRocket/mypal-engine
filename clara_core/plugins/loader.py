"""Plugin loader and discovery.

This module discovers plugins from multiple locations, loads their manifests,
and initializes them by calling their register() functions.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .types import (
        PluginManifest,
        PluginRecord,
        PluginContext,
        Diagnostic,
        PluginOrigin,
    )
    from .registry import PluginRegistry
    from .manifest import PluginManifestLoadResult

from .types import PluginKind, DiagnosticLevel, PluginOrigin
from .manifest import (
    load_plugin_manifest,
    resolve_manifest_path,
    validate_plugin_config,
)
from .registry import PluginRegistry

logger = logging.getLogger(__name__)


@dataclass
class PluginCandidate:
    """A candidate plugin discovered during scan."""

    id_hint: str
    source: str
    root_dir: Path
    origin: PluginOrigin
    workspace_dir: Path | None = None
    package_name: str | None = None
    package_version: str | None = None
    package_description: str | None = None


@dataclass
class PluginLoadOptions:
    """Options for plugin loading."""

    config: dict[str, Any] | None = None
    workspace_dir: Path | None = None
    logger: Any = None
    cache: bool = True
    mode: str = "full"  # "full" or "validate"


class PluginLoader:
    """Discovers, validates, and loads plugins.

    Searches multiple locations:
    - Bundled plugins (in codebase)
    - Global plugins (in ~/.mypalclara/plugins)
    - Workspace plugins (in .mypalclara/plugins)
    - Extra paths from config

    Usage:
        loader = PluginLoader()
        await loader.initialize(registry)
    """

    def __init__(
        self,
        bundled_dir: Path | None = None,
        global_dir: Path | None = None,
    ) -> None:
        """Initialize loader.

        Args:
            bundled_dir: Path to bundled plugins
            global_dir: Path to global plugins
        """
        self.bundled_dir = (
            bundled_dir
            or Path(__file__).parent.parent.parent / "plugins" / "bundled"
        )
        self.global_dir = (
            global_dir
            or Path.home() / ".mypalclara" / "plugins"
        )

        self._cache: dict[str, Any] = {}

    def discover(self, workspace_dir: Path | None = None) -> list[PluginCandidate]:
        """Discover all plugin candidates.

        Args:
            workspace_dir: Optional workspace directory

        Returns:
            List of PluginCandidate objects
        """
        candidates: list[PluginCandidate] = []
        seen = set()

        # 1. Bundled plugins
        candidates.extend(
            self._scan_directory(
                self.bundled_dir, PluginOrigin.BUNDLED, workspace_dir, seen
            )
        )

        # 2. Global plugins
        candidates.extend(
            self._scan_directory(
                self.global_dir, PluginOrigin.GLOBAL, workspace_dir, seen
            )
        )

        # 3. Workspace plugins
        if workspace_dir:
            workspace_plugins = workspace_dir / ".mypalclara" / "plugins"
            candidates.extend(
                self._scan_directory(
                    workspace_plugins, PluginOrigin.WORKSPACE, workspace_dir, seen
                )
            )

        # 4. Extra paths from config
        extra_paths = []
        if self._cache.get("config"):
            extra_paths = self._cache["config"].get("plugin_paths", [])

        for extra_path in extra_paths:
            candidates.extend(
                self._scan_path(
                    Path(extra_path), PluginOrigin.CONFIG, workspace_dir, seen
                )
            )

        logger.info(f"Discovered {len(candidates)} plugin candidates")
        return candidates

    def _scan_directory(
        self,
        dir: Path,
        origin: PluginOrigin,
        workspace_dir: Path | None,
        seen: set[Path],
    ) -> list[PluginCandidate]:
        """Scan a directory for plugins.

        Args:
            dir: Directory to scan
            origin: Origin type
            workspace_dir: Workspace directory
            seen: Set of seen paths (for deduplication)

        Returns:
            List of PluginCandidate objects
        """
        if not dir.exists():
            return []

        candidates = []

        for entry in dir.iterdir():
            if entry.name.startswith("_"):
                continue

            if entry.name.startswith("."):
                continue

            # Check if already seen
            resolved = entry.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)

            # Scan entry
            candidates.extend(
                self._scan_path(entry, origin, workspace_dir, seen)
            )

        return candidates

    def _scan_path(
        self,
        path: Path,
        origin: PluginOrigin,
        workspace_dir: Path | None,
        seen: set[Path],
    ) -> list[PluginCandidate]:
        """Scan a path for a plugin.

        Args:
            path: Path to scan (file or directory)
            origin: Origin type
            workspace_dir: Workspace directory
            seen: Set of seen paths

        Returns:
            List of PluginCandidate objects
        """
        # If it's a file, check if it's a Python file
        if path.is_file():
            if path.suffix == ".py":
                module_dir = path.parent
                return [
                    PluginCandidate(
                        id_hint=path.stem,
                        source=str(path),
                        root_dir=module_dir,
                        origin=origin,
                        workspace_dir=workspace_dir,
                    )
                ]
            return []

        # If it's a directory, look for index.py or package.json
        if path.is_dir():
            # Check for package.json (npm-style plugin)
            pkg_json = path / "package.json"
            if pkg_json.exists():
                try:
                    pkg_data = json.loads(pkg_json.read_text())
                    candidates = []

                    # Check for 'openclaw' metadata
                    if "openclaw" in pkg_data:
                        oc_meta = pkg_data["openclaw"]
                        extensions = oc_meta.get("extensions", [])
                        if isinstance(extensions, list):
                            for ext in extensions:
                                ext_path = path / ext
                                candidates.append(
                                    PluginCandidate(
                                        id_hint=f"{pkg_data.get('name', path.name)}/{ext_path.stem}",
                                        source=str(ext_path),
                                        root_dir=path,
                                        origin=origin,
                                        workspace_dir=workspace_dir,
                                        package_name=pkg_data.get("name"),
                                        package_version=pkg_data.get("version"),
                                        package_description=pkg_data.get("description"),
                                    )
                                )
                            return candidates
                except Exception as e:
                    logger.warning(f"Error reading package.json: {e}")

            # Check for clara.plugin.json (manifest-based)
            manifest = resolve_manifest_path(path)
            if manifest.exists():
                return [
                    PluginCandidate(
                        id_hint=path.name,
                        source=str(manifest),
                        root_dir=path,
                        origin=origin,
                        workspace_dir=workspace_dir,
                    )
                ]

            # Check for plugin.py (entry point)
            plugin_py = path / "plugin.py"
            if plugin_py.exists():
                return [
                    PluginCandidate(
                        id_hint=path.name,
                        source=str(plugin_py),
                        root_dir=path,
                        origin=origin,
                        workspace_dir=workspace_dir,
                    )
                ]

            # Check for index.py (directory as module)
            index_py = path / "index.py"
            if index_py.exists():
                return [
                    PluginCandidate(
                        id_hint=path.name,
                        source=str(index_py),
                        root_dir=path,
                        origin=origin,
                        workspace_dir=workspace_dir,
                    )
                ]

            # Otherwise, scan for .py files in directory
            return self._scan_directory(path, origin, workspace_dir, seen)

        return []

    async def load_all(
        self,
        registry: PluginRegistry,
        options: PluginLoadOptions | None = None,
    ) -> list[tuple[str, bool]]:
        """Load all discovered plugins.

        Args:
            registry: PluginRegistry to register with
            options: Loading options

        Returns:
            List of (plugin_id, success) tuples
        """
        if options:
            self._cache["config"] = options.config

        opts = options or PluginLoadOptions()
        workspace_dir = opts.workspace_dir

        results = []
        candidates = self.discover(workspace_dir)

        # Load manifests for all candidates
        for candidate in candidates:
            success = await self._load_candidate(
                registry, candidate, opts
            )
            results.append((candidate.id_hint, success))

        loaded_count = sum(1 for _, s in results if s)
        logger.info(f"Loaded {loaded_count}/{len(results)} plugins")

        return results

    async def _load_candidate(
        self,
        registry: PluginRegistry,
        candidate: PluginCandidate,
        options: PluginLoadOptions,
    ) -> bool:
        """Load a single plugin candidate.

        Args:
            registry: PluginRegistry to register with
            candidate: Plugin candidate to load
            options: Loading options

        Returns:
            True if plugin loaded successfully
        """
        # Load manifest
        manifest_result = load_plugin_manifest(candidate.root_dir)
        if not manifest_result.ok:
            logger.error(
                f"Failed to load manifest for {candidate.id_hint}: "
                f"{manifest_result.error}"
            )
            return False

        manifest = manifest_result.manifest

        # Use manifest ID if available
        plugin_id = manifest.id or candidate.id_hint

        # Create plugin record
        from .types import PluginRecord

        plugin_record = PluginRecord(
            id=plugin_id,
            name=manifest.name or plugin_id,
            version=manifest.version,
            description=manifest.description,
            kind=manifest.kind,
            origin=candidate.origin,
            source=candidate.source,
            workspace_dir=candidate.workspace_dir,
            enabled=True,
            status="loaded",
            error=None,
            config_schema=manifest.config_schema,
            config_ui_hints=manifest.config_ui_hints,
        )

        # Validate config
        plugin_config = {}
        if options.config and "plugins" in options.config:
            plugins_config = options.config.get("plugins", {})
            plugin_config = plugins_config.get(plugin_id, {})

            if manifest.config_schema:
                valid, validated, errors = validate_plugin_config(
                    manifest.config_schema, plugin_config
                )

                if not valid:
                    logger.error(
                        f"Invalid config for {plugin_id}: {', '.join(errors)}"
                    )
                    plugin_record.status = "error"
                    plugin_record.error = (
                        f"Invalid config: {', '.join(errors)}"
                    )
                    registry.register_plugin(plugin_record)
                    return False

                plugin_config = validated

        # Create plugin API
        from .types import PluginAPI
        from .runtime import PluginRuntime

        runtime = PluginRuntime(
            logger=logger,
            state_dir=registry.runtime.state_dir,
            config_dir=registry.runtime.config_dir,
        )

        plugin_api = PluginAPI(
            id=plugin_id,
            name=manifest.name or plugin_id,
            version=manifest.version,
            description=manifest.description,
            source=candidate.source,
            config=options.config or {},
            plugin_config=plugin_config,
            runtime=runtime,
            logger=logger,
            # Registration methods (bound to registry)
            register_tool=lambda t, opt=False: registry.register_tool(
                t, plugin_id, opt, candidate.source
            ),
            register_hook=lambda e, h: registry.register_hook(
                e, h, plugin_id, candidate.source
            ),
            register_channel=lambda c: registry.register_channel(c, plugin_id),
            register_provider=lambda p: registry.register_provider(
                p, plugin_id
            ),
            register_service=lambda s: registry.register_service(s, plugin_id),
            register_command=lambda cmd: None,  # TODO: implement
            resolve_path=lambda p: runtime.resolve_path(p),
        )

        # Load plugin module
        try:
            spec = importlib.util.spec_from_file_location(
                f"clara_plugin.{plugin_id}", candidate.source
            )

            if spec is None or spec.loader is None:
                logger.error(f"Failed to load spec for {plugin_id}")
                return False

            module = importlib.util.module_from_spec(spec)
            sys.modules[f"clara_plugin.{plugin_id}"] = module
            spec.loader.exec_module(module)

        except Exception as e:
            logger.error(f"Error loading plugin {plugin_id}: {e}", exc_info=True)
            plugin_record.status = "error"
            plugin_record.error = str(e)
            registry.register_plugin(plugin_record)
            return False

        # Call plugin's register() function
        try:
            register_func = None

            # Try different registration function names
            for attr_name in ("register", "activate", "setup"):
                if hasattr(module, attr_name):
                    register_func = getattr(module, attr_name)
                    break

            if register_func is None:
                logger.error(
                    f"Plugin {plugin_id} missing register() or activate() function"
                )
                plugin_record.status = "error"
                plugin_record.error = "Missing register function"
                registry.register_plugin(plugin_record)
                return False

            result = register_func(plugin_api)

            # Handle async register
            if asyncio.iscoroutine(result):
                await result

        except Exception as e:
            logger.error(
                f"Error during plugin {plugin_id} registration: {e}",
                exc_info=True,
            )
            plugin_record.status = "error"
            plugin_record.error = str(e)
            registry.register_plugin(plugin_record)
            return False

        # Register plugin record
        registry.register_plugin(plugin_record)
        logger.info(f"Successfully loaded plugin: {plugin_id}")
        return True
