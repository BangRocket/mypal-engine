"""Email tools for Clara.

Provides email checking and sending capabilities.
Tools: check_email, send_email

Requires: CLARA_EMAIL_ADDRESS, CLARA_EMAIL_PASSWORD env vars
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from ._base import ToolContext, ToolDef

if TYPE_CHECKING:
    pass

MODULE_NAME = "email"
MODULE_VERSION = "1.0.0"

# Configuration
EMAIL_ADDRESS = os.getenv("CLARA_EMAIL_ADDRESS", "")
EMAIL_PASSWORD = os.getenv("CLARA_EMAIL_PASSWORD", "")


def is_configured() -> bool:
    """Check if email is configured."""
    return bool(EMAIL_ADDRESS and EMAIL_PASSWORD)


def _get_monitor():
    """Get the email monitor singleton."""
    from email_monitor import get_email_monitor

    return get_email_monitor()


# --- Tool Handlers ---


async def check_email(args: dict[str, Any], ctx: ToolContext) -> str:
    """Check email inbox."""
    if not is_configured():
        return "Error: Email not configured. CLARA_EMAIL_ADDRESS and CLARA_EMAIL_PASSWORD must be set."

    monitor = _get_monitor()
    unread_only = args.get("unread_only", False)
    limit = min(args.get("limit", 10), 25)

    try:
        if unread_only:
            emails, error = monitor.check_emails(unseen_only=True)
        else:
            emails, error = monitor.get_all_emails(limit=limit)

        if error:
            return f"Error checking email: {error}"

        if not emails:
            return "No emails found." if not unread_only else "No unread emails."

        # Format results
        lines = [f"Found {len(emails)} email(s):\n"]
        for i, e in enumerate(emails, 1):
            status = " [UNREAD]" if not e.is_read else ""
            lines.append(f"{i}. **From:** {e.from_addr}")
            lines.append(f"   **Subject:** {e.subject}{status}")
            lines.append(f"   **Date:** {e.date}")
            if e.preview:
                lines.append(f"   **Preview:** {e.preview}")
            lines.append("")

        return "\n".join(lines)

    except Exception as e:
        return f"Error checking email: {str(e)}"


async def send_email(args: dict[str, Any], ctx: ToolContext) -> str:
    """Send an email."""
    if not is_configured():
        return "Error: Email not configured. CLARA_EMAIL_ADDRESS and CLARA_EMAIL_PASSWORD must be set."

    to_addr = args.get("to", "")
    subject = args.get("subject", "")
    body = args.get("body", "")

    if not to_addr or not subject or not body:
        return "Error: 'to', 'subject', and 'body' are all required."

    try:
        from email_monitor import send_email_smtp

        success, error = send_email_smtp(to_addr, subject, body)
        if success:
            return f"Email sent successfully to {to_addr}"
        return f"Error sending email: {error}"

    except Exception as e:
        return f"Error sending email: {str(e)}"


# --- Tool Definitions ---

TOOLS = [
    ToolDef(
        name="check_email",
        description=(
            "Check Clara's email inbox. Returns recent emails with sender, "
            "subject, and date. Use this when asked about email or to check "
            "for new messages."
        ),
        parameters={
            "type": "object",
            "properties": {
                "unread_only": {
                    "type": "boolean",
                    "description": (
                        "If true, only show unread emails. "
                        "Default is false (show all recent)."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": (
                        "Maximum number of emails to return (default: 10, max: 25)"
                    ),
                },
            },
            "required": [],
        },
        handler=check_email,
        requires=["email"],
    ),
    ToolDef(
        name="send_email",
        description="Send an email from Clara's email address.",
        parameters={
            "type": "object",
            "properties": {
                "to": {
                    "type": "string",
                    "description": "Recipient email address",
                },
                "subject": {
                    "type": "string",
                    "description": "Email subject line",
                },
                "body": {
                    "type": "string",
                    "description": "Email body text",
                },
            },
            "required": ["to", "subject", "body"],
        },
        handler=send_email,
        requires=["email"],
    ),
]


# --- Lifecycle Hooks ---


async def initialize() -> None:
    """Initialize email module."""
    if is_configured():
        print(f"[email] Configured for {EMAIL_ADDRESS}")
    else:
        print("[email] Not configured - tools will be disabled")


async def cleanup() -> None:
    """Cleanup on module unload."""
    pass
