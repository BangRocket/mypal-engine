"""Obsidian tool module — 16 tools for reading/writing/searching the user's vault.

Tools are registered in `core_tools/__init__.py` and become available in Clara's
inventory only when the user has configured an Obsidian token (via the
per-user availability predicate `has_obsidian_config`).

Tools are grouped into four families:
- Read: list_vault, list_dir, get_file, get_active_file, get_periodic_note, list_tags, list_commands
- Search: search, query (DQL or JsonLogic)
- Write: create_or_update_file, append_to_file, patch_file, append_to_periodic_note, delete_file
- UI / commands: open_file, execute_command

Write tools invalidate the per-user vault snapshot cache on success so the
next prompt build reflects the mutation.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any

from mypalclara.core.obsidian.exceptions import (
    ObsidianAuthError,
    ObsidianConnectionError,
    ObsidianError,
    ObsidianNotFoundError,
)
from mypalclara.core.obsidian.factory import get_client_for_user
from mypalclara.tools._base import ToolContext, ToolDef

MODULE_NAME = "obsidian"
MODULE_VERSION = "1.0.0"
logger = logging.getLogger("clara.tools.obsidian")


SYSTEM_PROMPT = """\
You have read/write access to the user's Obsidian vault via the obsidian-local-rest-api plugin.

Principles:
- Prefer `obsidian_search` before `obsidian_get_file` when you do not know the exact path.
- For targeted edits, prefer `obsidian_patch_file` (heading/block/frontmatter) over
  `obsidian_create_or_update_file`, which overwrites the entire note.
- Periodic notes are the user's journal. `obsidian_append_to_periodic_note` with
  `period="daily"` is the right default for "add this to my journal" / "log this".
- `obsidian_open_file` surfaces a note in the user's Obsidian UI. Use sparingly — only
  when the user explicitly asks to see something, not as a background step.
- Write tools mutate the user's vault. The effects are visible to the user, so think
  before you edit: prefer append over overwrite, prefer patch over create_or_update.
- If a tool returns "Obsidian not configured", the user must set up the integration
  via their profile settings. Do not retry.
- Note paths in the vault are relative, no leading slash: "Projects/foo.md", not "/Projects/foo.md".
"""


async def has_obsidian_config(canonical_user_id: str) -> bool:
    """Availability predicate — True if the user has configured Obsidian.

    Used by the gateway's per-user tool filter (ToolDef.availability). Looks up
    credentials via the identity service; a None client means "not configured"
    and the tools are hidden from this user's inventory.
    """
    try:
        client = await get_client_for_user(canonical_user_id)
        return client is not None
    except Exception:
        logger.warning("has_obsidian_config failed", exc_info=True)
        return False


async def _client_or_error(ctx: ToolContext):
    """Return the user's ObsidianClient, or an error string to return to the LLM."""
    client = await get_client_for_user(ctx.user_id)
    if client is None:
        return None, "Obsidian is not configured for this user."
    return client, None


def _format_obsidian_error(path: str | None, e: Exception) -> str:
    if isinstance(e, ObsidianAuthError):
        return "Obsidian authentication failed. Please update your API token."
    if isinstance(e, ObsidianNotFoundError):
        return f"Note not found: {path}" if path else "Resource not found."
    if isinstance(e, ObsidianConnectionError):
        return f"Obsidian unreachable: {e}"
    if isinstance(e, ObsidianError):
        return f"Obsidian error: {e}"
    return f"Unexpected error: {e}"


# ---- read handlers (E2) ----


async def _handle_list_vault(args: dict[str, Any], ctx: ToolContext) -> str:
    client, err = await _client_or_error(ctx)
    if err:
        return err
    try:
        files = await client.list_vault()
        return json.dumps(files)
    except ObsidianError as e:
        return _format_obsidian_error(None, e)


async def _handle_list_dir(args: dict[str, Any], ctx: ToolContext) -> str:
    path = args.get("path", "").strip()
    if not path:
        return "Error: 'path' is required."
    client, err = await _client_or_error(ctx)
    if err:
        return err
    try:
        files = await client.list_dir(path)
        return json.dumps(files)
    except ObsidianError as e:
        return _format_obsidian_error(path, e)


async def _handle_get_file(args: dict[str, Any], ctx: ToolContext) -> str:
    path = args.get("path", "").strip()
    if not path:
        return "Error: 'path' is required."
    client, err = await _client_or_error(ctx)
    if err:
        return err
    try:
        return await client.get_file(path)
    except ObsidianError as e:
        return _format_obsidian_error(path, e)


async def _handle_get_active_file(args: dict[str, Any], ctx: ToolContext) -> str:
    client, err = await _client_or_error(ctx)
    if err:
        return err
    try:
        return await client.get_active()
    except ObsidianError as e:
        return _format_obsidian_error(None, e)


async def _handle_get_periodic_note(args: dict[str, Any], ctx: ToolContext) -> str:
    period = args.get("period", "").strip().lower()
    if period not in ("daily", "weekly", "monthly", "quarterly", "yearly"):
        return "Error: 'period' must be one of daily, weekly, monthly, quarterly, yearly."
    date_str = args.get("date")
    parsed_date = None
    if date_str:
        try:
            parsed_date = date.fromisoformat(date_str)
        except ValueError:
            return f"Error: 'date' must be YYYY-MM-DD (got {date_str!r})."
    client, err = await _client_or_error(ctx)
    if err:
        return err
    try:
        return await client.get_periodic(period, parsed_date)
    except ObsidianError as e:
        return _format_obsidian_error(f"{period} note", e)


async def _handle_list_tags(args: dict[str, Any], ctx: ToolContext) -> str:
    client, err = await _client_or_error(ctx)
    if err:
        return err
    try:
        tags = await client.list_tags()
        # Return a structured list for Clara to consume
        return json.dumps([{"tag": name, "count": count} for name, count in tags])
    except ObsidianError as e:
        return _format_obsidian_error(None, e)


async def _handle_list_commands(args: dict[str, Any], ctx: ToolContext) -> str:
    client, err = await _client_or_error(ctx)
    if err:
        return err
    try:
        commands = await client.list_commands()
        return json.dumps(commands)
    except ObsidianError as e:
        return _format_obsidian_error(None, e)


# ---- tools populated by E2-E5 ----

TOOLS: list[ToolDef] = [
    ToolDef(
        name="obsidian_list_vault",
        description="List files and directories at the root of the user's Obsidian vault.",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=_handle_list_vault,
        availability=has_obsidian_config,
        risk_level="safe",
        intent="read",
        emoji="📁",
    ),
    ToolDef(
        name="obsidian_list_dir",
        description="List files and directories at a specific path in the Obsidian vault.",
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Vault-relative directory path, e.g. 'Projects'.",
                }
            },
            "required": ["path"],
        },
        handler=_handle_list_dir,
        availability=has_obsidian_config,
        risk_level="safe",
        intent="read",
        emoji="📁",
        detail_keys=["path"],
    ),
    ToolDef(
        name="obsidian_get_file",
        description="Read the full content of a note in the Obsidian vault.",
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Vault-relative file path, e.g. 'Projects/foo.md'.",
                }
            },
            "required": ["path"],
        },
        handler=_handle_get_file,
        availability=has_obsidian_config,
        risk_level="safe",
        intent="read",
        emoji="📄",
        detail_keys=["path"],
    ),
    ToolDef(
        name="obsidian_get_active_file",
        description="Read the content of the note currently open in the user's Obsidian UI.",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=_handle_get_active_file,
        availability=has_obsidian_config,
        risk_level="safe",
        intent="read",
        emoji="📄",
    ),
    ToolDef(
        name="obsidian_get_periodic_note",
        description=(
            "Read the user's periodic note (daily/weekly/monthly/quarterly/yearly). "
            "Defaults to today's note for the chosen period."
        ),
        parameters={
            "type": "object",
            "properties": {
                "period": {
                    "type": "string",
                    "enum": ["daily", "weekly", "monthly", "quarterly", "yearly"],
                    "description": "Which periodic note to read.",
                },
                "date": {
                    "type": "string",
                    "description": ("Optional ISO date (YYYY-MM-DD). If omitted, reads the " "current period's note."),
                },
            },
            "required": ["period"],
        },
        handler=_handle_get_periodic_note,
        availability=has_obsidian_config,
        risk_level="safe",
        intent="read",
        emoji="📆",
        detail_keys=["period", "date"],
    ),
    ToolDef(
        name="obsidian_list_tags",
        description="List all tags in the user's Obsidian vault with usage counts.",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=_handle_list_tags,
        availability=has_obsidian_config,
        risk_level="safe",
        intent="read",
        emoji="🏷️",
    ),
    ToolDef(
        name="obsidian_list_commands",
        description="List available Obsidian commands the user could execute.",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=_handle_list_commands,
        availability=has_obsidian_config,
        risk_level="safe",
        intent="read",
        emoji="⚙️",
    ),
]
