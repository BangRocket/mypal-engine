"""ORS Notes management tools.

Allows users to view, manage, and interact with their ORS (Organic Response System)
notes - Clara's internal observations and follow-ups about the user.

Tools: ors_list_notes, ors_view_note, ors_archive_note, ors_add_note
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from db.connection import SessionLocal
from db.models import ProactiveNote, UserInteractionPattern

from ._base import ToolContext, ToolDef

MODULE_NAME = "ors_notes"
MODULE_VERSION = "1.0.0"

SYSTEM_PROMPT = """
## Internal Notes (ORS)
You maintain internal notes about users - observations, questions to follow up on,
and connections you've noticed. Users can view and manage these notes.

**Tools:**
- `ors_list_notes` - List your pending notes about the user
- `ors_view_note` - View details of a specific note
- `ors_archive_note` - Archive a note that's no longer relevant
- `ors_add_note` - Manually add a note to track something

**Note Types:**
- observation: Something you noticed about them
- question: Something to check on later
- follow_up: Explicit thing to follow up on
- connection: Pattern or dot-connection between things

**When a user asks to see your notes, use these tools transparently.**
""".strip()


# --- Tool Handlers ---


async def ors_list_notes(args: dict[str, Any], ctx: ToolContext) -> str:
    """List the user's ORS notes."""
    include_archived = args.get("include_archived", False)
    note_type = args.get("note_type")
    limit = min(args.get("limit", 20), 50)

    with SessionLocal() as session:
        query = session.query(ProactiveNote).filter(
            ProactiveNote.user_id == ctx.user_id,
        )

        if not include_archived:
            query = query.filter(ProactiveNote.archived == "false")

        if note_type:
            query = query.filter(ProactiveNote.note_type == note_type)

        notes = query.order_by(ProactiveNote.relevance_score.desc()).limit(limit).all()

        if not notes:
            return "No notes found. I haven't accumulated any observations yet."

        result = []
        for n in notes:
            status = []
            if n.surfaced == "true":
                status.append("surfaced")
            if n.archived == "true":
                status.append("archived")
            if n.expires_at:
                if n.expires_at < datetime.now(UTC).replace(tzinfo=None):
                    status.append("expired")
                else:
                    hours_left = (n.expires_at - datetime.now(UTC).replace(tzinfo=None)).total_seconds() / 3600
                    status.append(f"expires in {hours_left:.0f}h")

            status_str = f" ({', '.join(status)})" if status else ""
            type_str = f"[{n.note_type}]" if n.note_type else "[note]"
            created = n.created_at.strftime("%Y-%m-%d") if n.created_at else "?"

            result.append(
                f"• {type_str} {n.note[:100]}{'...' if len(n.note) > 100 else ''}\n"
                f"  ID: {n.id[:8]}... | Relevance: {n.relevance_score}% | "
                f"Created: {created}{status_str}"
            )

        header = f"Found {len(notes)} note(s):\n\n"
        return header + "\n\n".join(result)


def _find_note_by_partial_id(session, user_id: str, partial_id: str) -> tuple[ProactiveNote | None, str | None]:
    """Find a note by partial ID with helpful error messages.

    Returns: (note, error_message) - one will always be None
    """
    partial_id = partial_id.strip().lower()

    # Try exact match first
    note = (
        session.query(ProactiveNote)
        .filter(
            ProactiveNote.user_id == user_id,
            ProactiveNote.id == partial_id,
        )
        .first()
    )
    if note:
        return note, None

    # Try prefix match (case-insensitive)
    matches = (
        session.query(ProactiveNote)
        .filter(
            ProactiveNote.user_id == user_id,
            ProactiveNote.id.ilike(f"{partial_id}%"),
        )
        .limit(5)
        .all()
    )

    if not matches:
        return None, f"No notes found matching '{partial_id}'. Use ors_list_notes to see available notes."

    if len(matches) == 1:
        return matches[0], None

    # Multiple matches - show them
    match_list = "\n".join(f"  • {n.id[:12]}... - {n.note[:40]}..." for n in matches)
    return None, f"Multiple notes match '{partial_id}':\n{match_list}\nPlease use more characters to be specific."


async def ors_view_note(args: dict[str, Any], ctx: ToolContext) -> str:
    """View details of a specific note."""
    note_id = args.get("note_id", "")
    if not note_id:
        return "Error: note_id is required"

    with SessionLocal() as session:
        note, error = _find_note_by_partial_id(session, ctx.user_id, note_id)
        if error:
            return error

        # Format full note details
        lines = [
            "**Note Details**",
            f"ID: {note.id}",
            f"Type: {note.note_type or 'untyped'}",
            "",
            "**Content:**",
            note.note,
            "",
            "**Metadata:**",
            f"- Relevance: {note.relevance_score}%",
            f"- Created: {note.created_at.isoformat() if note.created_at else '?'}",
            f"- Surfaced: {note.surfaced}",
            f"- Archived: {note.archived}",
        ]

        if note.expires_at:
            lines.append(f"- Expires: {note.expires_at.isoformat()}")

        if note.surface_conditions:
            lines.append(f"- Surface conditions: {note.surface_conditions}")

        if note.source_context:
            lines.append(f"- Source context: {note.source_context[:200]}...")

        return "\n".join(lines)


async def ors_archive_note(args: dict[str, Any], ctx: ToolContext) -> str:
    """Archive a note that's no longer relevant."""
    note_id = args.get("note_id", "")
    if not note_id:
        return "Error: note_id is required"

    with SessionLocal() as session:
        note, error = _find_note_by_partial_id(session, ctx.user_id, note_id)
        if error:
            return error

        if note.archived == "true":
            return "This note is already archived."

        note.archived = "true"
        session.commit()

        return f"Archived note: {note.note[:50]}..."


async def ors_add_note(args: dict[str, Any], ctx: ToolContext) -> str:
    """Manually add a note to track something about the user."""
    content = args.get("content", "").strip()
    note_type = args.get("note_type", "observation")
    expires_hours = args.get("expires_hours")

    if not content:
        return "Error: content is required"

    # Validate note type
    valid_types = ["observation", "question", "follow_up", "connection"]
    if note_type not in valid_types:
        return f"Error: note_type must be one of: {', '.join(valid_types)}"

    # Calculate expiration
    expires_at = None
    if expires_hours:
        try:
            hours = int(expires_hours)
            if 1 <= hours <= 168:
                expires_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=hours)
        except ValueError:
            return "Error: expires_hours must be a number between 1 and 168"

    with SessionLocal() as session:
        note = ProactiveNote(
            user_id=ctx.user_id,
            note=content,
            note_type=note_type,
            expires_at=expires_at,
            relevance_score=100,
            source_context=json.dumps({"from": "user_request"}),
        )
        session.add(note)
        session.commit()

        result = f"Added {note_type}: {content[:50]}..."
        if expires_at:
            result += f" (expires in {expires_hours}h)"
        return result


async def ors_open_threads(args: dict[str, Any], ctx: ToolContext) -> str:
    """View or manage open threads (unresolved topics) for the user."""
    action = args.get("action", "list")
    thread_content = args.get("thread")

    with SessionLocal() as session:
        pattern = session.query(UserInteractionPattern).filter(UserInteractionPattern.user_id == ctx.user_id).first()

        if not pattern:
            return "No interaction history found yet."

        # Get existing threads
        threads = []
        if pattern.open_threads:
            try:
                threads = json.loads(pattern.open_threads)
            except json.JSONDecodeError:
                threads = []

        if action == "list":
            if not threads:
                return "No open threads being tracked."
            result = "**Open threads (unresolved topics):**\n\n"
            for i, thread in enumerate(threads, 1):
                result += f"{i}. {thread}\n"
            return result

        elif action == "add" and thread_content:
            if thread_content not in threads:
                threads.insert(0, thread_content)
                pattern.open_threads = json.dumps(threads[:10])
                session.commit()
            return f"Added open thread: {thread_content}"

        elif action == "resolve" and thread_content:
            thread_lower = thread_content.lower()
            original_count = len(threads)
            threads = [t for t in threads if thread_lower not in t.lower()]
            if len(threads) < original_count:
                pattern.open_threads = json.dumps(threads) if threads else None
                session.commit()
                return f"Resolved thread matching: {thread_content}"
            return f"No thread found matching: {thread_content}"

        else:
            return "Invalid action. Use 'list', 'add', or 'resolve'."


# --- Tool Definitions ---

TOOLS = [
    ToolDef(
        name="ors_list_notes",
        description=(
            "List your internal notes/observations about the user. "
            "Shows pending notes with type, relevance, and status. "
            "Use when user asks 'what do you know about me' or 'show me your notes'."
        ),
        parameters={
            "type": "object",
            "properties": {
                "include_archived": {
                    "type": "boolean",
                    "description": "Include archived notes (default: false)",
                },
                "note_type": {
                    "type": "string",
                    "enum": ["observation", "question", "follow_up", "connection"],
                    "description": "Filter by note type",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max notes to return (default: 20, max: 50)",
                },
            },
            "required": [],
        },
        handler=ors_list_notes,
    ),
    ToolDef(
        name="ors_view_note",
        description=(
            "View full details of a specific note by ID. " "Supports partial ID matching (first few characters)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "note_id": {
                    "type": "string",
                    "description": "Note ID (or partial ID)",
                },
            },
            "required": ["note_id"],
        },
        handler=ors_view_note,
    ),
    ToolDef(
        name="ors_archive_note",
        description=(
            "Archive a note that's no longer relevant. " "Use when user says something is resolved or outdated."
        ),
        parameters={
            "type": "object",
            "properties": {
                "note_id": {
                    "type": "string",
                    "description": "Note ID (or partial ID) to archive",
                },
            },
            "required": ["note_id"],
        },
        handler=ors_archive_note,
    ),
    ToolDef(
        name="ors_add_note",
        description=(
            "Add a note to track something about the user. " "Use for explicit reminders, follow-ups, or observations."
        ),
        parameters={
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "The note content",
                },
                "note_type": {
                    "type": "string",
                    "enum": ["observation", "question", "follow_up", "connection"],
                    "description": "Type of note (default: observation)",
                },
                "expires_hours": {
                    "type": "integer",
                    "description": "Hours until note expires (1-168, optional)",
                },
            },
            "required": ["content"],
        },
        handler=ors_add_note,
    ),
    ToolDef(
        name="ors_open_threads",
        description=(
            "View or manage open threads - unresolved topics from conversations. "
            "Actions: 'list' (default), 'add', 'resolve'."
        ),
        parameters={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "add", "resolve"],
                    "description": "Action to perform (default: list)",
                },
                "thread": {
                    "type": "string",
                    "description": "Thread content (for add/resolve)",
                },
            },
            "required": [],
        },
        handler=ors_open_threads,
    ),
]


# --- Lifecycle Hooks ---


async def initialize() -> None:
    """Initialize ORS notes module."""
    print("[ors_notes] Loaded - user note management tools available")


async def cleanup() -> None:
    """Cleanup on module unload."""
    pass
