"""Plugin manifest loading and validation.

This module handles loading and validating plugin manifest files
(clara.plugin.json), following OpenClaw's manifest format.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .types import (
    Diagnostic,
    DiagnosticLevel,
    PluginKind,
    PluginManifest,
)

logger = logging.getLogger(__name__)

# Manifest filenames to search for
PLUGIN_MANIFEST_FILENAMES = [
    "clara.plugin.json",
    "plugin.json",
    "manifest.json",
]


@dataclass
class PluginManifestLoadResult:
    """Result of loading a plugin manifest."""

    ok: bool
    manifest: PluginManifest | None
    error: str | None
    manifest_path: str | None


def resolve_manifest_path(root_dir: Path) -> Path:
    """Find the plugin manifest file in a directory.

    Searches for common manifest filenames.

    Args:
        root_dir: Directory to search for manifest

    Returns:
        Path to manifest file (may not exist)
    """
    for filename in PLUGIN_MANIFEST_FILENAMES:
        candidate = root_dir / filename
        if candidate.exists():
            return candidate
    # Default to first option
    return root_dir / PLUGIN_MANIFEST_FILENAMES[0]


def load_plugin_manifest(root_dir: Path) -> PluginManifestLoadResult:
    """Load and validate a plugin manifest file.

    Args:
        root_dir: Directory containing the plugin

    Returns:
        PluginManifestLoadResult with manifest or error
    """
    manifest_path = resolve_manifest_path(root_dir)

    if not manifest_path.exists():
        return PluginManifestLoadResult(
            ok=False,
            manifest=None,
            error=f"plugin manifest not found: {manifest_path}",
            manifest_path=str(manifest_path),
        )

    try:
        raw = manifest_path.read_text()
        data: dict[str, Any] = json.loads(raw)
    except json.JSONDecodeError as e:
        return PluginManifestLoadResult(
            ok=False,
            manifest=None,
            error=f"failed to parse plugin manifest: {e}",
            manifest_path=str(manifest_path),
        )
    except Exception as e:
        return PluginManifestLoadResult(
            ok=False,
            manifest=None,
            error=f"failed to read plugin manifest: {e}",
            manifest_path=str(manifest_path),
        )

    if not isinstance(data, dict):
        return PluginManifestLoadResult(
            ok=False,
            manifest=None,
            error="plugin manifest must be an object",
            manifest_path=str(manifest_path),
        )

    # Validate required fields
    plugin_id = data.get("id")
    if not isinstance(plugin_id, str) or not plugin_id.strip():
        return PluginManifestLoadResult(
            ok=False,
            manifest=None,
            error="plugin manifest requires 'id' field",
            manifest_path=str(manifest_path),
        )

    # Extract optional fields
    name = _extract_string(data, "name")
    version = _extract_string(data, "version")
    description = _extract_string(data, "description")

    # Parse kind
    kind_str = _extract_string(data, "kind")
    kind = None
    if kind_str:
        try:
            kind = PluginKind(kind_str)
        except ValueError:
            logger.warning(f"Unknown plugin kind: {kind_str}")

    # Config schema
    config_schema = data.get("configSchema")
    if config_schema is not None and not isinstance(config_schema, dict):
        config_schema = None

    # Config UI hints
    config_ui_hints = data.get("uiHints")
    if config_ui_hints is not None and not isinstance(config_ui_hints, dict):
        config_ui_hints = None

    # Optional feature lists
    tools = _extract_string_list(data, "tools")
    hooks = _extract_string_list(data, "hooks")
    channels = _extract_string_list(data, "channels")
    providers = _extract_string_list(data, "providers")

    return PluginManifestLoadResult(
        ok=True,
        manifest=PluginManifest(
            id=plugin_id.strip(),
            name=name,
            version=version,
            description=description,
            kind=kind,
            config_schema=config_schema,
            config_ui_hints=config_ui_hints,
            tools=tools,
            hooks=hooks,
            channels=channels,
            providers=providers,
        ),
        error=None,
        manifest_path=str(manifest_path),
    )


def validate_plugin_config(
    schema: dict[str, Any] | None,
    raw_config: dict[str, Any] | None,
) -> tuple[bool, dict[str, Any], list[str]]:
    """Validate plugin configuration against schema.

    Simple validation - in production, use jsonschema or similar.

    Args:
        schema: JSON Schema for config (or None)
        raw_config: Raw config values

    Returns:
        (valid, validated_config, errors) tuple
    """
    if schema is None:
        return True, raw_config or {}, []

    if raw_config is None:
        raw_config = {}

    errors = []
    validated = {}

    # Simple validation: check required fields and types
    properties = schema.get("properties", {})
    required = schema.get("required", [])

    for key, prop_def in properties.items():
        if key in required and key not in raw_config:
            errors.append(f"Missing required config field: {key}")
            continue

        value = raw_config.get(key)
        prop_type = prop_def.get("type")

        if value is None:
            if key in required:
                errors.append(f"Required field missing: {key}")
            continue

        # Basic type checking
        if prop_type == "string" and not isinstance(value, str):
            errors.append(f"Field '{key}' should be string, got {type(value)}")
        elif prop_type == "boolean" and not isinstance(value, bool):
            errors.append(f"Field '{key}' should be boolean, got {type(value)}")
        elif prop_type == "number" and not isinstance(value, (int, float)):
            errors.append(f"Field '{key}' should be number, got {type(value)}")
        elif prop_type == "array" and not isinstance(value, list):
            errors.append(f"Field '{key}' should be array, got {type(value)}")
        elif prop_type == "object" and not isinstance(value, dict):
            errors.append(f"Field '{key}' should be object, got {type(value)}")

        if not errors or key not in [e.split(":")[-1].strip() for e in errors]:
            validated[key] = value

    return len(errors) == 0, validated, errors


def _extract_string(data: dict[str, Any], key: str) -> str | None:
    """Extract a string field from dict, returning None if not string."""
    value = data.get(key)
    if isinstance(value, str):
        return value.strip() if value.strip() else None
    return None


def _extract_string_list(data: dict[str, Any], key: str) -> list[str] | None:
    """Extract a string list field from dict."""
    value = data.get(key)
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        result = []
        for item in value:
            if isinstance(item, str):
                result.append(item.strip())
        return result if result else None
    return None
