"""Email Alerts tools for Clara.

Provides tools for users to configure email monitoring accounts and rules.
Supports Gmail (via existing Google OAuth) and IMAP (app-specific passwords).
"""

from __future__ import annotations

import json
import os
from typing import Any

from ._base import ToolContext, ToolDef
from db.connection import SessionLocal
from db.models import EmailAccount, EmailAlert, EmailRule
from email_service.credentials import encrypt_credential, is_encryption_configured
from email_service.presets import apply_preset, get_preset_info, list_presets, remove_preset
from email_service.providers.gmail import GmailProvider
from email_service.providers.imap import IMAPProvider
from tools.google_oauth import is_user_connected

# API service base URL for OAuth redirects
CLARA_API_URL = os.getenv("CLARA_API_URL", "")

MODULE_NAME = "email_alerts"
MODULE_VERSION = "1.0.0"

SYSTEM_PROMPT = """
## Email Monitoring

You can help users monitor their email accounts and get Discord alerts for important messages.

**Account Management:**
- `email_connect_gmail` - Connect Gmail using existing Google account
- `email_connect_imap` - Connect IMAP account (iCloud, Outlook, etc.)
- `email_list_accounts` - List connected email accounts
- `email_disconnect` - Disconnect an email account

**Configuration:**
- `email_set_alert_channel` - Set Discord channel for alerts
- `email_set_quiet_hours` - Configure quiet hours (no alerts)
- `email_toggle_ping` - Toggle @mentions on alerts

**Rules:**
- `email_apply_preset` - Apply built-in rule preset (job_hunting, urgent, security, etc.)
- `email_list_presets` - List available rule presets
- `email_add_rule` - Add custom rule
- `email_list_rules` - List configured rules
- `email_remove_rule` - Remove a rule

**Status:**
- `email_status` - Check email monitoring status
- `email_recent_alerts` - View recent alerts

Gmail requires an existing Google connection (use `google_connect` first).
IMAP accounts require app-specific passwords (not regular passwords).
""".strip()


# =============================================================================
# Account Management
# =============================================================================


async def email_connect_gmail(args: dict[str, Any], ctx: ToolContext) -> str:
    """Connect Gmail for monitoring using existing Google OAuth."""
    user_id = ctx.user_id

    # Check if Google is connected
    if not is_user_connected(user_id):
        return (
            "You need to connect your Google account first. "
            "Use `google_connect` to connect, then try again. "
            "Make sure to grant email access when authorizing."
        )

    # Check if Gmail account already exists
    with SessionLocal() as session:
        existing = (
            session.query(EmailAccount)
            .filter(
                EmailAccount.user_id == user_id,
                EmailAccount.provider_type == "gmail",
            )
            .first()
        )

        if existing:
            return f"Gmail is already connected: {existing.email_address}"

        # Test Gmail connection
        test_account = EmailAccount(
            user_id=user_id,
            email_address="(testing)",
            provider_type="gmail",
        )

    provider = GmailProvider(test_account)
    success, error = await provider.test_connection()

    if not success:
        return (
            f"Failed to connect Gmail: {error}\n\n"
            "This usually means you need to reconnect Google with email permissions. "
            "Use `google_disconnect` then `google_connect` to re-authorize."
        )

    # Get email address from profile
    from tools.google_oauth import get_valid_token
    import httpx

    token = await get_valid_token(user_id)
    async with httpx.AsyncClient() as client:
        response = await client.get(
            "https://gmail.googleapis.com/gmail/v1/users/me/profile",
            headers={"Authorization": f"Bearer {token}"},
        )
        if response.status_code == 200:
            email_address = response.json().get("emailAddress", "unknown@gmail.com")
        else:
            email_address = "unknown@gmail.com"

    # Create account
    with SessionLocal() as session:
        account = EmailAccount(
            user_id=user_id,
            email_address=email_address,
            provider_type="gmail",
            enabled="true",
            poll_interval_minutes=5,
            status="active",
        )
        session.add(account)
        session.commit()

        return (
            f"Gmail connected successfully: {email_address}\n\n"
            "Next steps:\n"
            "1. Use `email_set_alert_channel` to choose where to receive alerts\n"
            "2. Use `email_apply_preset` to add rules (e.g., job_hunting, urgent)\n"
            "3. Or use `email_add_rule` to create custom rules"
        )


async def email_connect_imap(args: dict[str, Any], ctx: ToolContext) -> str:
    """Connect an IMAP email account."""
    user_id = ctx.user_id
    email_address = args.get("email_address", "").strip()
    password = args.get("password", "").strip()
    imap_server = args.get("imap_server", "").strip()
    imap_port = args.get("imap_port", 993)

    if not email_address or not password:
        return "Both email_address and password are required."

    if not is_encryption_configured():
        return (
            "Email encryption is not configured. "
            "Please set EMAIL_ENCRYPTION_KEY environment variable."
        )

    # Auto-detect IMAP server if not provided
    if not imap_server:
        domain = email_address.split("@")[-1].lower()
        imap_servers = {
            "icloud.com": "imap.mail.me.com",
            "me.com": "imap.mail.me.com",
            "mac.com": "imap.mail.me.com",
            "outlook.com": "outlook.office365.com",
            "hotmail.com": "outlook.office365.com",
            "live.com": "outlook.office365.com",
            "yahoo.com": "imap.mail.yahoo.com",
            "gmail.com": "imap.gmail.com",  # Though Gmail OAuth is preferred
        }
        imap_server = imap_servers.get(domain, f"imap.{domain}")

    # Check if account already exists
    with SessionLocal() as session:
        existing = (
            session.query(EmailAccount)
            .filter(
                EmailAccount.user_id == user_id,
                EmailAccount.email_address == email_address,
            )
            .first()
        )

        if existing:
            return f"Account already connected: {email_address}"

        # Test connection
        test_account = EmailAccount(
            user_id=user_id,
            email_address=email_address,
            provider_type="imap",
            imap_server=imap_server,
            imap_port=imap_port,
            imap_username=email_address,
            imap_password=encrypt_credential(password),
        )

    provider = IMAPProvider(test_account)
    success, error = await provider.test_connection()

    if not success:
        return (
            f"Failed to connect: {error}\n\n"
            "Make sure you're using an app-specific password, not your regular password. "
            "For iCloud, generate one at appleid.apple.com > Security > App-Specific Passwords."
        )

    # Save account
    with SessionLocal() as session:
        account = EmailAccount(
            user_id=user_id,
            email_address=email_address,
            provider_type="imap",
            imap_server=imap_server,
            imap_port=imap_port,
            imap_username=email_address,
            imap_password=encrypt_credential(password),
            enabled="true",
            poll_interval_minutes=5,
            status="active",
        )
        session.add(account)
        session.commit()

        return (
            f"Email account connected: {email_address}\n\n"
            "Next steps:\n"
            "1. Use `email_set_alert_channel` to choose where to receive alerts\n"
            "2. Use `email_apply_preset` to add rules (e.g., job_hunting, urgent)"
        )


async def email_list_accounts(args: dict[str, Any], ctx: ToolContext) -> str:
    """List connected email accounts."""
    user_id = ctx.user_id

    with SessionLocal() as session:
        accounts = (
            session.query(EmailAccount)
            .filter(EmailAccount.user_id == user_id)
            .all()
        )

        if not accounts:
            return "No email accounts connected. Use `email_connect_gmail` or `email_connect_imap` to add one."

        lines = ["**Connected Email Accounts:**\n"]
        for acc in accounts:
            status_emoji = "✅" if acc.status == "active" else "⚠️" if acc.status == "error" else "🔴"
            channel = f"<#{acc.alert_channel_id}>" if acc.alert_channel_id else "Not set"
            lines.append(
                f"{status_emoji} **{acc.email_address}** ({acc.provider_type})\n"
                f"   Status: {acc.status} | Alerts: {channel} | Interval: {acc.poll_interval_minutes}min"
            )
            if acc.last_error:
                lines.append(f"   Last error: {acc.last_error[:100]}")

        return "\n".join(lines)


async def email_disconnect(args: dict[str, Any], ctx: ToolContext) -> str:
    """Disconnect an email account."""
    user_id = ctx.user_id
    email_address = args.get("email_address", "").strip()

    if not email_address:
        return "Please specify the email_address to disconnect."

    with SessionLocal() as session:
        account = (
            session.query(EmailAccount)
            .filter(
                EmailAccount.user_id == user_id,
                EmailAccount.email_address == email_address,
            )
            .first()
        )

        if not account:
            return f"Account not found: {email_address}"

        # Delete associated rules and alerts
        session.query(EmailRule).filter(EmailRule.account_id == account.id).delete()
        session.query(EmailAlert).filter(EmailAlert.account_id == account.id).delete()
        session.delete(account)
        session.commit()

        return f"Disconnected: {email_address}"


# =============================================================================
# Configuration
# =============================================================================


async def email_set_alert_channel(args: dict[str, Any], ctx: ToolContext) -> str:
    """Set Discord channel for email alerts."""
    user_id = ctx.user_id
    email_address = args.get("email_address", "").strip()
    channel_id = args.get("channel_id", "").strip()

    if not channel_id:
        # Try to use current channel
        channel_id = ctx.channel_id or ""

    if not channel_id:
        return "Please specify a channel_id or use this command in the channel you want alerts sent to."

    # Clean channel ID (handle <#123> format)
    channel_id = channel_id.replace("<#", "").replace(">", "")

    with SessionLocal() as session:
        if email_address:
            # Update specific account
            account = (
                session.query(EmailAccount)
                .filter(
                    EmailAccount.user_id == user_id,
                    EmailAccount.email_address == email_address,
                )
                .first()
            )
            if not account:
                return f"Account not found: {email_address}"

            account.alert_channel_id = channel_id
            session.commit()
            return f"Alert channel set to <#{channel_id}> for {email_address}"
        else:
            # Update all accounts
            accounts = (
                session.query(EmailAccount)
                .filter(EmailAccount.user_id == user_id)
                .all()
            )
            if not accounts:
                return "No email accounts found."

            for acc in accounts:
                acc.alert_channel_id = channel_id
            session.commit()
            return f"Alert channel set to <#{channel_id}> for all {len(accounts)} account(s)"


async def email_set_quiet_hours(args: dict[str, Any], ctx: ToolContext) -> str:
    """Set quiet hours (no alerts during this time)."""
    user_id = ctx.user_id
    start_hour = args.get("start_hour")
    end_hour = args.get("end_hour")
    email_address = args.get("email_address", "").strip()

    if start_hour is None or end_hour is None:
        return "Please specify both start_hour and end_hour (0-23)."

    if not (0 <= start_hour <= 23 and 0 <= end_hour <= 23):
        return "Hours must be between 0 and 23."

    with SessionLocal() as session:
        if email_address:
            accounts = [
                session.query(EmailAccount)
                .filter(
                    EmailAccount.user_id == user_id,
                    EmailAccount.email_address == email_address,
                )
                .first()
            ]
        else:
            accounts = (
                session.query(EmailAccount)
                .filter(EmailAccount.user_id == user_id)
                .all()
            )

        if not accounts or not accounts[0]:
            return "No email accounts found."

        for acc in accounts:
            if acc:
                acc.quiet_hours_start = start_hour
                acc.quiet_hours_end = end_hour
        session.commit()

        return f"Quiet hours set: {start_hour}:00 - {end_hour}:00 (no alerts during this time)"


async def email_toggle_ping(args: dict[str, Any], ctx: ToolContext) -> str:
    """Toggle @mention on alerts."""
    user_id = ctx.user_id
    email_address = args.get("email_address", "").strip()
    enabled = args.get("enabled", True)

    with SessionLocal() as session:
        if email_address:
            accounts = [
                session.query(EmailAccount)
                .filter(
                    EmailAccount.user_id == user_id,
                    EmailAccount.email_address == email_address,
                )
                .first()
            ]
        else:
            accounts = (
                session.query(EmailAccount)
                .filter(EmailAccount.user_id == user_id)
                .all()
            )

        if not accounts or not accounts[0]:
            return "No email accounts found."

        for acc in accounts:
            if acc:
                acc.ping_on_alert = "true" if enabled else "false"
        session.commit()

        status = "enabled" if enabled else "disabled"
        return f"@mentions on alerts: {status}"


# =============================================================================
# Rules
# =============================================================================


async def email_apply_preset(args: dict[str, Any], ctx: ToolContext) -> str:
    """Apply a built-in rule preset."""
    user_id = ctx.user_id
    preset_name = args.get("preset_name", "").strip().lower()

    if not preset_name:
        presets = list_presets()
        lines = ["Available presets:\n"]
        for p in presets:
            lines.append(f"- **{p['id']}**: {p['description']} (importance: {p['importance']})")
        return "\n".join(lines)

    info = get_preset_info(preset_name)
    if not info:
        return f"Unknown preset: {preset_name}. Use `email_apply_preset` without arguments to see available presets."

    rule = apply_preset(user_id, preset_name)
    if rule:
        return f"Applied preset '{info['name']}': {info['description']}"
    else:
        return f"Failed to apply preset: {preset_name}"


async def email_list_presets(args: dict[str, Any], ctx: ToolContext) -> str:
    """List available rule presets."""
    presets = list_presets()
    lines = ["**Available Rule Presets:**\n"]
    for p in presets:
        lines.append(f"**{p['id']}** - {p['name']}")
        lines.append(f"  {p['description']}")
        lines.append(f"  Importance: {p['importance']}\n")
    return "\n".join(lines)


async def email_add_rule(args: dict[str, Any], ctx: ToolContext) -> str:
    """Add a custom email rule."""
    user_id = ctx.user_id
    name = args.get("name", "").strip()
    importance = args.get("importance", "normal")

    # Build conditions from args
    conditions = {}
    if args.get("sender_contains"):
        conditions["sender_contains"] = args["sender_contains"]
    if args.get("sender_domain"):
        conditions["sender_domain"] = args["sender_domain"]
    if args.get("subject_contains"):
        conditions["subject_contains"] = args["subject_contains"]
    if args.get("subject_regex"):
        conditions["subject_regex"] = args["subject_regex"]

    if not name:
        return "Rule name is required."

    if not conditions:
        return (
            "At least one condition is required. Options:\n"
            "- sender_contains: list of strings to match in sender\n"
            "- sender_domain: list of domains to match\n"
            "- subject_contains: list of strings to match in subject\n"
            "- subject_regex: regex pattern for subject"
        )

    rule_definition = {
        "conditions": conditions,
        "match_mode": args.get("match_mode", "any"),
    }

    with SessionLocal() as session:
        rule = EmailRule(
            user_id=user_id,
            name=name,
            enabled="true",
            priority=args.get("priority", 0),
            rule_definition=json.dumps(rule_definition),
            importance=importance,
        )
        session.add(rule)
        session.commit()

        return f"Rule created: {name} (importance: {importance})"


async def email_list_rules(args: dict[str, Any], ctx: ToolContext) -> str:
    """List configured email rules."""
    user_id = ctx.user_id

    with SessionLocal() as session:
        rules = (
            session.query(EmailRule)
            .filter(EmailRule.user_id == user_id)
            .order_by(EmailRule.priority.desc())
            .all()
        )

        if not rules:
            return "No rules configured. Use `email_apply_preset` or `email_add_rule` to add rules."

        lines = ["**Email Rules:**\n"]
        for rule in rules:
            enabled_emoji = "✅" if rule.enabled == "true" else "❌"
            preset_tag = f" (preset: {rule.preset_name})" if rule.preset_name else ""
            lines.append(
                f"{enabled_emoji} **{rule.name}**{preset_tag}\n"
                f"   Importance: {rule.importance} | Priority: {rule.priority}"
            )

        return "\n".join(lines)


async def email_remove_rule(args: dict[str, Any], ctx: ToolContext) -> str:
    """Remove an email rule."""
    user_id = ctx.user_id
    rule_name = args.get("rule_name", "").strip()
    preset_name = args.get("preset_name", "").strip()

    if preset_name:
        success = remove_preset(user_id, preset_name)
        if success:
            return f"Removed preset: {preset_name}"
        return f"Preset not found: {preset_name}"

    if not rule_name:
        return "Please specify rule_name or preset_name to remove."

    with SessionLocal() as session:
        rule = (
            session.query(EmailRule)
            .filter(
                EmailRule.user_id == user_id,
                EmailRule.name == rule_name,
            )
            .first()
        )

        if not rule:
            return f"Rule not found: {rule_name}"

        session.delete(rule)
        session.commit()
        return f"Removed rule: {rule_name}"


# =============================================================================
# Status
# =============================================================================


async def email_status(args: dict[str, Any], ctx: ToolContext) -> str:
    """Check email monitoring status."""
    user_id = ctx.user_id

    with SessionLocal() as session:
        accounts = (
            session.query(EmailAccount)
            .filter(EmailAccount.user_id == user_id)
            .all()
        )
        rules = (
            session.query(EmailRule)
            .filter(EmailRule.user_id == user_id)
            .all()
        )

        if not accounts:
            return (
                "Email monitoring is not set up.\n\n"
                "Get started:\n"
                "1. Connect an account: `email_connect_gmail` or `email_connect_imap`\n"
                "2. Set alert channel: `email_set_alert_channel`\n"
                "3. Add rules: `email_apply_preset job_hunting`"
            )

        lines = ["**Email Monitoring Status**\n"]

        for acc in accounts:
            status_emoji = "✅" if acc.status == "active" else "⚠️"
            channel = f"<#{acc.alert_channel_id}>" if acc.alert_channel_id else "Not set"
            last_check = acc.last_checked_at.strftime("%Y-%m-%d %H:%M UTC") if acc.last_checked_at else "Never"

            lines.append(f"{status_emoji} **{acc.email_address}** ({acc.provider_type})")
            lines.append(f"   Alert channel: {channel}")
            lines.append(f"   Last checked: {last_check}")
            lines.append(f"   Poll interval: {acc.poll_interval_minutes} minutes")

            if acc.quiet_hours_start is not None:
                lines.append(f"   Quiet hours: {acc.quiet_hours_start}:00 - {acc.quiet_hours_end}:00")

            if acc.last_error:
                lines.append(f"   ⚠️ Error: {acc.last_error[:80]}")

            lines.append("")

        lines.append(f"**Rules configured:** {len(rules)}")
        if rules:
            rule_names = [r.name for r in rules[:5]]
            lines.append(f"   {', '.join(rule_names)}")
            if len(rules) > 5:
                lines.append(f"   ... and {len(rules) - 5} more")

        return "\n".join(lines)


async def email_recent_alerts(args: dict[str, Any], ctx: ToolContext) -> str:
    """View recent email alerts."""
    user_id = ctx.user_id
    limit = args.get("limit", 10)

    with SessionLocal() as session:
        alerts = (
            session.query(EmailAlert)
            .filter(EmailAlert.user_id == user_id)
            .order_by(EmailAlert.sent_at.desc())
            .limit(limit)
            .all()
        )

        if not alerts:
            return "No recent alerts."

        lines = ["**Recent Email Alerts:**\n"]
        for alert in alerts:
            time_str = alert.sent_at.strftime("%m/%d %H:%M")
            importance_emoji = {"urgent": "🔴", "high": "🟠", "normal": "🔵", "low": "⚪"}.get(
                alert.importance, "⚪"
            )
            lines.append(
                f"{importance_emoji} [{time_str}] {alert.email_subject[:50]}\n"
                f"   From: {alert.email_from[:40]}"
            )

        return "\n".join(lines)


# =============================================================================
# Tool Definitions
# =============================================================================


TOOLS = [
    # Account Management
    ToolDef(
        name="email_connect_gmail",
        description="Connect Gmail for email monitoring (uses existing Google connection).",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=email_connect_gmail,
        requires=["email_monitoring"],
    ),
    ToolDef(
        name="email_connect_imap",
        description="Connect an IMAP email account (iCloud, Outlook, etc.) for monitoring.",
        parameters={
            "type": "object",
            "properties": {
                "email_address": {
                    "type": "string",
                    "description": "Email address to connect",
                },
                "password": {
                    "type": "string",
                    "description": "App-specific password (NOT regular password)",
                },
                "imap_server": {
                    "type": "string",
                    "description": "IMAP server (auto-detected if omitted)",
                },
                "imap_port": {
                    "type": "integer",
                    "description": "IMAP port (default: 993)",
                    "default": 993,
                },
            },
            "required": ["email_address", "password"],
        },
        handler=email_connect_imap,
        requires=["email_monitoring"],
    ),
    ToolDef(
        name="email_list_accounts",
        description="List connected email accounts.",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=email_list_accounts,
        requires=["email_monitoring"],
    ),
    ToolDef(
        name="email_disconnect",
        description="Disconnect an email account.",
        parameters={
            "type": "object",
            "properties": {
                "email_address": {
                    "type": "string",
                    "description": "Email address to disconnect",
                },
            },
            "required": ["email_address"],
        },
        handler=email_disconnect,
        requires=["email_monitoring"],
    ),
    # Configuration
    ToolDef(
        name="email_set_alert_channel",
        description="Set Discord channel for email alerts.",
        parameters={
            "type": "object",
            "properties": {
                "channel_id": {
                    "type": "string",
                    "description": "Discord channel ID (or omit to use current channel)",
                },
                "email_address": {
                    "type": "string",
                    "description": "Specific account (or omit for all accounts)",
                },
            },
            "required": [],
        },
        handler=email_set_alert_channel,
        requires=["email_monitoring"],
    ),
    ToolDef(
        name="email_set_quiet_hours",
        description="Set quiet hours (no alerts during this time).",
        parameters={
            "type": "object",
            "properties": {
                "start_hour": {
                    "type": "integer",
                    "description": "Start hour (0-23)",
                },
                "end_hour": {
                    "type": "integer",
                    "description": "End hour (0-23)",
                },
                "email_address": {
                    "type": "string",
                    "description": "Specific account (or omit for all accounts)",
                },
            },
            "required": ["start_hour", "end_hour"],
        },
        handler=email_set_quiet_hours,
        requires=["email_monitoring"],
    ),
    ToolDef(
        name="email_toggle_ping",
        description="Toggle @mentions on email alerts.",
        parameters={
            "type": "object",
            "properties": {
                "enabled": {
                    "type": "boolean",
                    "description": "Enable @mentions",
                    "default": True,
                },
                "email_address": {
                    "type": "string",
                    "description": "Specific account (or omit for all accounts)",
                },
            },
            "required": [],
        },
        handler=email_toggle_ping,
        requires=["email_monitoring"],
    ),
    # Rules
    ToolDef(
        name="email_apply_preset",
        description="Apply a built-in rule preset (job_hunting, urgent, security, financial, shipping).",
        parameters={
            "type": "object",
            "properties": {
                "preset_name": {
                    "type": "string",
                    "description": "Preset name (omit to list available presets)",
                },
            },
            "required": [],
        },
        handler=email_apply_preset,
        requires=["email_monitoring"],
    ),
    ToolDef(
        name="email_list_presets",
        description="List available rule presets.",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=email_list_presets,
        requires=["email_monitoring"],
    ),
    ToolDef(
        name="email_add_rule",
        description="Add a custom email rule.",
        parameters={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Rule name",
                },
                "importance": {
                    "type": "string",
                    "enum": ["low", "normal", "high", "urgent"],
                    "description": "Alert importance level",
                    "default": "normal",
                },
                "sender_contains": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Strings to match in sender address",
                },
                "sender_domain": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Domains to match",
                },
                "subject_contains": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Strings to match in subject",
                },
                "subject_regex": {
                    "type": "string",
                    "description": "Regex pattern for subject",
                },
                "match_mode": {
                    "type": "string",
                    "enum": ["any", "all"],
                    "description": "Match any condition or all conditions",
                    "default": "any",
                },
                "priority": {
                    "type": "integer",
                    "description": "Rule priority (higher = checked first)",
                    "default": 0,
                },
            },
            "required": ["name"],
        },
        handler=email_add_rule,
        requires=["email_monitoring"],
    ),
    ToolDef(
        name="email_list_rules",
        description="List configured email rules.",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=email_list_rules,
        requires=["email_monitoring"],
    ),
    ToolDef(
        name="email_remove_rule",
        description="Remove an email rule.",
        parameters={
            "type": "object",
            "properties": {
                "rule_name": {
                    "type": "string",
                    "description": "Rule name to remove",
                },
                "preset_name": {
                    "type": "string",
                    "description": "Preset name to remove",
                },
            },
            "required": [],
        },
        handler=email_remove_rule,
        requires=["email_monitoring"],
    ),
    # Status
    ToolDef(
        name="email_status",
        description="Check email monitoring status.",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=email_status,
        requires=["email_monitoring"],
    ),
    ToolDef(
        name="email_recent_alerts",
        description="View recent email alerts.",
        parameters={
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Number of alerts to show",
                    "default": 10,
                },
            },
            "required": [],
        },
        handler=email_recent_alerts,
        requires=["email_monitoring"],
    ),
]
