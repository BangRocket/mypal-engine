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

from mypalclara.core.obsidian import _snapshot_cache
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
The user's Obsidian vault is your **durable, shared memory** with them.
Unlike your Palace (your internal scratch notes), the vault is visible to the
user in their own Obsidian app — so writes persist, can be edited by either
of you, and form a shared record you both return to.

When to READ the vault:
- Before answering questions about the user's projects, decisions, ongoing
  work, or past conversations — `obsidian_search` first, then `obsidian_get_file`
  for the most promising hits. The User Context block above names top-level
  folders and recent edits; use them to narrow search.
- When the user references "my notes on X" / "what did I say about Y" /
  "that doc we made" — the vault is the authoritative source, not your
  Palace memory.
- To load context for a task (a project's README, a design doc, an ongoing
  checklist) before making changes or suggestions.

When to WRITE to the vault:
- Decisions, plans, and conclusions the user will want to come back to later.
  Prefer `obsidian_append_to_periodic_note` with `period="daily"` for routine
  log entries, and `obsidian_patch_file` on a project note (under its relevant
  heading) for work-stream updates.
- Structured notes the user asks you to create — drafts, outlines, summaries,
  reading lists, literature notes. Use the existing top-level folder structure
  shown in the User Context block; do not invent new top-level folders without
  asking.
- Stable facts about the user that the user has explicitly told you they want
  remembered (e.g. "remember that I use Poetry, not pip"). Put them where the
  user can find them — a `Reference/` note or daily-note summary — not only
  in your Palace.

What NOT to write to the vault:
- Your own internal chain-of-thought, uncertain hypotheses, or anything the
  user didn't explicitly or implicitly want captured. The vault is THEIR
  notebook, not your scratchpad.
- Ephemeral conversational acknowledgements ("yes", "ok"). Those go nowhere.
- Sensitive data (API keys, passwords, tokens) — keep those in the identity
  service, never in vault notes.

How to edit safely:
- Prefer `obsidian_search` before `obsidian_get_file` when you do not know
  the exact path.
- Prefer `obsidian_patch_file` (heading/block/frontmatter) over
  `obsidian_create_or_update_file` — patch targets a specific section;
  create-or-update REPLACES the whole note.
- `obsidian_append_to_file` is the safest mutation for adding to an existing
  note.
- `obsidian_delete_file` is destructive; confirm with the user before using it
  unless they explicitly asked you to delete.
- `obsidian_open_file` surfaces a note in the user's Obsidian UI. Use sparingly
  — only when the user explicitly asks to see something, not as a background step.

Conventions:
- Paths are vault-relative with no leading slash: `Projects/foo.md`, not
  `/Projects/foo.md`.
- If a tool returns "Obsidian not configured", the user must set up the
  integration in their profile settings. Do not retry in that turn.
- When you do write to the vault, briefly tell the user WHERE you wrote and
  WHY — so they can audit and so they know the memory is there for next time.
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


# ---- search handlers (E3) ----


async def _handle_search(args: dict[str, Any], ctx: ToolContext) -> str:
    query = args.get("query", "").strip()
    if not query:
        return "Error: 'query' is required."
    context_length = args.get("context_length")
    if context_length is not None:
        try:
            context_length = int(context_length)
        except (TypeError, ValueError):
            return "Error: 'context_length' must be an integer."
        if context_length < 0:
            return "Error: 'context_length' must be non-negative."
    client, err = await _client_or_error(ctx)
    if err:
        return err
    try:
        hits = await client.search_simple(query, context_length=context_length)
        return json.dumps(hits)
    except ObsidianError as e:
        return _format_obsidian_error(None, e)


async def _handle_query(args: dict[str, Any], ctx: ToolContext) -> str:
    query_type = args.get("query_type", "").strip().lower()
    query = args.get("query")
    if query_type not in ("dql", "jsonlogic"):
        return "Error: 'query_type' must be 'dql' or 'jsonlogic'."
    if query is None or (isinstance(query, str) and not query.strip()):
        return "Error: 'query' is required."

    # Validate argument shape per dialect before resolving the client, so
    # argument errors surface consistently regardless of configuration state.
    if query_type == "dql":
        if not isinstance(query, str):
            return "Error: DQL 'query' must be a string."
        query_payload: str | dict = query
    else:  # jsonlogic
        # Accept either a dict directly or a JSON string that parses to a dict.
        if isinstance(query, str):
            try:
                query_obj = json.loads(query)
            except json.JSONDecodeError:
                return "Error: JsonLogic 'query' must be a JSON object or JSON string."
        else:
            query_obj = query
        if not isinstance(query_obj, dict):
            return "Error: JsonLogic 'query' must decode to an object."
        query_payload = query_obj

    client, err = await _client_or_error(ctx)
    if err:
        return err

    try:
        if query_type == "dql":
            results = await client.search_dql(query_payload)  # type: ignore[arg-type]
        else:
            results = await client.search_jsonlogic(query_payload)  # type: ignore[arg-type]
        return json.dumps(results)
    except ObsidianError as e:
        return _format_obsidian_error(None, e)


# ---- write handlers (E4) ----


async def _handle_create_or_update_file(args: dict[str, Any], ctx: ToolContext) -> str:
    path = args.get("path", "").strip()
    content = args.get("content", "")
    if not path:
        return "Error: 'path' is required."
    if content is None:
        return "Error: 'content' is required."
    client, err = await _client_or_error(ctx)
    if err:
        return err
    try:
        await client.put_file(path, content)
        _snapshot_cache.invalidate(ctx.user_id)
        return f"Wrote {path}."
    except ObsidianError as e:
        return _format_obsidian_error(path, e)


async def _handle_append_to_file(args: dict[str, Any], ctx: ToolContext) -> str:
    path = args.get("path", "").strip()
    content = args.get("content", "")
    if not path:
        return "Error: 'path' is required."
    if not content:
        return "Error: 'content' is required."
    client, err = await _client_or_error(ctx)
    if err:
        return err
    try:
        await client.append_file(path, content)
        _snapshot_cache.invalidate(ctx.user_id)
        return f"Appended to {path}."
    except ObsidianError as e:
        return _format_obsidian_error(path, e)


async def _handle_patch_file(args: dict[str, Any], ctx: ToolContext) -> str:
    path = args.get("path", "").strip()
    target_type = args.get("target_type", "").strip().lower()
    target = args.get("target", "").strip() if isinstance(args.get("target"), str) else ""
    content = args.get("content", "")
    operation = args.get("operation", "append").strip().lower()

    if not path:
        return "Error: 'path' is required."
    if target_type not in ("heading", "block", "frontmatter"):
        return "Error: 'target_type' must be one of heading, block, frontmatter."
    if not target:
        return "Error: 'target' is required."
    if operation not in ("append", "prepend", "replace"):
        return "Error: 'operation' must be one of append, prepend, replace."
    if content is None:
        return "Error: 'content' is required."

    client, err = await _client_or_error(ctx)
    if err:
        return err
    try:
        await client.patch_file(path, target_type, target, content, operation=operation)
        _snapshot_cache.invalidate(ctx.user_id)
        return f"Patched {path} ({operation} at {target_type}={target})."
    except ObsidianError as e:
        return _format_obsidian_error(path, e)


async def _handle_append_to_periodic_note(args: dict[str, Any], ctx: ToolContext) -> str:
    period = args.get("period", "").strip().lower()
    content = args.get("content", "")
    date_str = args.get("date")

    if period not in ("daily", "weekly", "monthly", "quarterly", "yearly"):
        return "Error: 'period' must be one of daily, weekly, monthly, quarterly, yearly."
    if not content:
        return "Error: 'content' is required."

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
        await client.append_periodic(period, content, parsed_date)
        _snapshot_cache.invalidate(ctx.user_id)
        suffix = f" ({date_str})" if date_str else ""
        return f"Appended to {period} note{suffix}."
    except ObsidianError as e:
        return _format_obsidian_error(f"{period} note", e)


async def _handle_delete_file(args: dict[str, Any], ctx: ToolContext) -> str:
    path = args.get("path", "").strip()
    if not path:
        return "Error: 'path' is required."
    client, err = await _client_or_error(ctx)
    if err:
        return err
    try:
        await client.delete_file(path)
        _snapshot_cache.invalidate(ctx.user_id)
        return f"Deleted {path}."
    except ObsidianError as e:
        return _format_obsidian_error(path, e)


# ---- UI / command handlers (E5) ----
#
# These are write-intent (user-visible effect) but they do NOT invalidate the
# snapshot cache: opening a note doesn't mutate the vault, and most Obsidian
# commands don't either. If Clara chooses to run a command that does mutate
# (e.g. a bulk-rename plugin command), the next write-path tool call will
# invalidate — keeping invalidation coupled to explicit writes keeps things
# simple.


async def _handle_open_file(args: dict[str, Any], ctx: ToolContext) -> str:
    path = args.get("path", "").strip()
    if not path:
        return "Error: 'path' is required."
    client, err = await _client_or_error(ctx)
    if err:
        return err
    try:
        await client.open_file(path)
        return f"Opened {path} in the Obsidian UI."
    except ObsidianError as e:
        return _format_obsidian_error(path, e)


async def _handle_execute_command(args: dict[str, Any], ctx: ToolContext) -> str:
    command_id = args.get("command_id", "").strip()
    if not command_id:
        return "Error: 'command_id' is required."
    client, err = await _client_or_error(ctx)
    if err:
        return err
    try:
        await client.execute_command(command_id)
        return f"Executed Obsidian command: {command_id}"
    except ObsidianError as e:
        return _format_obsidian_error(command_id, e)


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
    ToolDef(
        name="obsidian_search",
        description="Full-text search across the user's Obsidian vault. Returns hit paths with match excerpts.",
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Text to search for in note contents.",
                },
                "context_length": {
                    "type": "integer",
                    "description": "Optional chars of context around each match (default: API default, ~100).",
                },
            },
            "required": ["query"],
        },
        handler=_handle_search,
        availability=has_obsidian_config,
        risk_level="safe",
        intent="read",
        emoji="🔎",
        detail_keys=["query"],
    ),
    ToolDef(
        name="obsidian_query",
        description=(
            "Run a structured query over the vault. query_type='dql' runs a "
            "Dataview DQL query (requires the Dataview plugin). query_type='jsonlogic' "
            "runs a JsonLogic query. For DQL, 'query' is a string like "
            "'TABLE file.mtime FROM \"\" SORT file.mtime DESC LIMIT 5'. For JsonLogic, "
            "'query' is a JSON object or a JSON-encoded string."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query_type": {
                    "type": "string",
                    "enum": ["dql", "jsonlogic"],
                    "description": "Which structured-query dialect.",
                },
                "query": {
                    "type": "string",
                    "description": (
                        "For query_type=dql, a DQL query string (e.g. "
                        "'TABLE file.mtime FROM \"\" SORT file.mtime DESC LIMIT 5'). "
                        "For query_type=jsonlogic, a JSON-encoded JsonLogic object "
                        "(e.g. '{\"in\": [\"clara\", {\"var\": \"file.tags\"}]}')."
                    ),
                },
            },
            "required": ["query_type", "query"],
        },
        handler=_handle_query,
        availability=has_obsidian_config,
        risk_level="safe",
        intent="read",
        emoji="🔎",
        detail_keys=["query_type"],
    ),
    ToolDef(
        name="obsidian_create_or_update_file",
        description=(
            "Create a new note or REPLACE an existing note entirely. Prefer "
            "obsidian_append_to_file or obsidian_patch_file for targeted edits."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Vault-relative file path."},
                "content": {"type": "string", "description": "Full markdown content."},
            },
            "required": ["path", "content"],
        },
        handler=_handle_create_or_update_file,
        availability=has_obsidian_config,
        risk_level="moderate",
        intent="write",
        emoji="✍️",
        detail_keys=["path"],
    ),
    ToolDef(
        name="obsidian_append_to_file",
        description="Append markdown content to the end of an existing note (or create it).",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Vault-relative file path."},
                "content": {"type": "string", "description": "Content to append."},
            },
            "required": ["path", "content"],
        },
        handler=_handle_append_to_file,
        availability=has_obsidian_config,
        risk_level="moderate",
        intent="write",
        emoji="➕",
        detail_keys=["path"],
    ),
    ToolDef(
        name="obsidian_patch_file",
        description=(
            "Insert content relative to a heading, block ID, or frontmatter field. "
            "Prefer this over obsidian_create_or_update_file for targeted edits."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Vault-relative file path."},
                "target_type": {
                    "type": "string",
                    "enum": ["heading", "block", "frontmatter"],
                    "description": "What kind of insertion point to target.",
                },
                "target": {
                    "type": "string",
                    "description": "Heading path (e.g. 'H1::H2'), block ID, or frontmatter field name.",
                },
                "content": {"type": "string", "description": "Content to insert."},
                "operation": {
                    "type": "string",
                    "enum": ["append", "prepend", "replace"],
                    "description": "How to combine content with the target (default: append).",
                },
            },
            "required": ["path", "target_type", "target", "content"],
        },
        handler=_handle_patch_file,
        availability=has_obsidian_config,
        risk_level="moderate",
        intent="write",
        emoji="🔧",
        detail_keys=["path", "target_type", "target"],
    ),
    ToolDef(
        name="obsidian_append_to_periodic_note",
        description=(
            "Append markdown to the user's periodic (daily/weekly/etc.) note. "
            "Default is today's note for the chosen period."
        ),
        parameters={
            "type": "object",
            "properties": {
                "period": {
                    "type": "string",
                    "enum": ["daily", "weekly", "monthly", "quarterly", "yearly"],
                },
                "content": {"type": "string", "description": "Content to append."},
                "date": {
                    "type": "string",
                    "description": "Optional ISO date (YYYY-MM-DD); defaults to today.",
                },
            },
            "required": ["period", "content"],
        },
        handler=_handle_append_to_periodic_note,
        availability=has_obsidian_config,
        risk_level="moderate",
        intent="write",
        emoji="📝",
        detail_keys=["period", "date"],
    ),
    ToolDef(
        name="obsidian_delete_file",
        description="Permanently delete a note from the vault. This cannot be undone via Clara.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Vault-relative file path."},
            },
            "required": ["path"],
        },
        handler=_handle_delete_file,
        availability=has_obsidian_config,
        risk_level="dangerous",
        intent="write",
        emoji="🗑️",
        detail_keys=["path"],
    ),
    ToolDef(
        name="obsidian_open_file",
        description=(
            "Open a note in the user's Obsidian UI. The user will see the note pop "
            "open. Use sparingly — only when the user explicitly wants to see something."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Vault-relative file path to open.",
                },
            },
            "required": ["path"],
        },
        handler=_handle_open_file,
        availability=has_obsidian_config,
        risk_level="safe",
        intent="write",  # user-visible effect
        emoji="👁️",
        detail_keys=["path"],
    ),
    ToolDef(
        name="obsidian_execute_command",
        description=(
            "Run an Obsidian command by its ID (e.g. 'editor:save-file'). Use "
            "obsidian_list_commands to discover available command IDs."
        ),
        parameters={
            "type": "object",
            "properties": {
                "command_id": {
                    "type": "string",
                    "description": "Obsidian command ID, e.g. 'editor:save-file'.",
                },
            },
            "required": ["command_id"],
        },
        handler=_handle_execute_command,
        availability=has_obsidian_config,
        risk_level="moderate",
        intent="write",
        emoji="⚡",
        detail_keys=["command_id"],
    ),
]
