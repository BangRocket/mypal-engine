"""Organic Response System (ORS) package.

Provides proactive conversation capabilities through a state machine approach:
    WAIT  - No action needed. Stay quiet, but keep gathering context.
    THINK - Something's brewing. Process and file it away as a note.
    SPEAK - There's a reason to reach out now with clear purpose.
"""

from mypalclara.services.proactive.engine import (
    ORS_ENABLED,
    ORSContext,
    ORSDecision,
    ORSState,
    ValidatedNote,
    add_open_thread,
    gather_full_context,
    get_active_users,
    is_enabled,
    on_proactive_response,
    on_user_message,
    ors_main_loop,
    process_user,
    resolve_open_thread,
    send_proactive_message,
)

__all__ = [
    "ORS_ENABLED",
    "ORSContext",
    "ORSDecision",
    "ORSState",
    "ValidatedNote",
    "add_open_thread",
    "gather_full_context",
    "get_active_users",
    "is_enabled",
    "on_proactive_response",
    "on_user_message",
    "ors_main_loop",
    "process_user",
    "resolve_open_thread",
    "send_proactive_message",
]
