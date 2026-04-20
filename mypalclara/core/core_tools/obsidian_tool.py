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

import logging

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


# ---- tools populated by E2-E5 ----

TOOLS: list[ToolDef] = []
