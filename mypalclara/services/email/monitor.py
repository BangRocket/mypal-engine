"""Email monitoring background service.

Polls email accounts and sends Discord alerts for important messages.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import discord

from mypalclara.config.logging import get_logger
from mypalclara.db.connection import SessionLocal
from mypalclara.db.models import EmailAccount, EmailAlert, EmailRule
from mypalclara.services.email.providers.base import EmailMessage, EmailProvider
from mypalclara.services.email.providers.gmail import GmailProvider
from mypalclara.services.email.providers.imap import IMAPProvider
from mypalclara.services.email.rules_engine import RuleMatch, evaluate_email

if TYPE_CHECKING:
    from discord import Client

logger = get_logger("email.monitor")

# Configuration
EMAIL_MONITORING_ENABLED = os.getenv("EMAIL_MONITORING_ENABLED", "false").lower() == "true"
EMAIL_DEFAULT_POLL_INTERVAL = int(os.getenv("EMAIL_DEFAULT_POLL_INTERVAL", "5"))
EMAIL_ERROR_BACKOFF_MAX = int(os.getenv("EMAIL_ERROR_BACKOFF_MAX", "60"))

# Track next check times per account
_next_checks: dict[str, datetime] = {}


def is_email_monitoring_enabled() -> bool:
    """Check if email monitoring is enabled."""
    return EMAIL_MONITORING_ENABLED


def get_provider(account: EmailAccount) -> EmailProvider:
    """Get appropriate provider for account type."""
    if account.provider_type == "gmail":
        return GmailProvider(account)
    else:
        return IMAPProvider(account)


async def email_monitor_loop(client: Client) -> None:
    """Main email monitoring loop.

    Started via self.loop.create_task() in Discord bot's on_ready().
    """
    logger.info("Email monitoring loop starting...")

    # Wait for bot to be ready
    await client.wait_until_ready()

    # Initial delay to let things settle
    await asyncio.sleep(10)

    while not client.is_closed():
        try:
            now = datetime.now(UTC).replace(tzinfo=None)

            # Get all enabled accounts that need checking
            accounts = get_accounts_to_check(now)

            for account in accounts:
                try:
                    await check_account(account, client)
                except Exception as e:
                    logger.error(f"Error checking account {account.email_address}: {e}")
                    await handle_account_error(account, str(e))

                # Small delay between accounts to avoid overwhelming
                await asyncio.sleep(1)

            # Calculate sleep duration until next account needs checking
            sleep_seconds = calculate_next_sleep()
            await asyncio.sleep(min(60, max(10, sleep_seconds)))

        except Exception as e:
            logger.error(f"Email monitor loop error: {e}")
            await asyncio.sleep(60)


def get_accounts_to_check(now: datetime) -> list[EmailAccount]:
    """Get accounts that are due for checking."""
    with SessionLocal() as session:
        accounts = (
            session.query(EmailAccount)
            .filter(
                EmailAccount.enabled == "true",
                EmailAccount.status != "disabled",
            )
            .all()
        )

        # Filter to accounts that need checking
        due_accounts = []
        for account in accounts:
            account_id = account.id
            next_check = _next_checks.get(account_id)

            if next_check is None or now >= next_check:
                # Expunge from session so we can use outside
                session.expunge(account)
                due_accounts.append(account)

        return due_accounts


def calculate_next_sleep() -> int:
    """Calculate seconds until next account needs checking."""
    if not _next_checks:
        return 60

    now = datetime.now(UTC).replace(tzinfo=None)
    soonest = min(_next_checks.values())
    delta = (soonest - now).total_seconds()
    return max(10, int(delta))


async def check_account(account: EmailAccount, client: Client) -> None:
    """Check a single account for new emails."""
    logger.debug(f"Checking email account: {account.email_address}")

    # Get provider
    provider = get_provider(account)

    try:
        async with provider:
            # Fetch new messages
            messages = await provider.get_new_messages(
                since_uid=account.last_seen_uid,
                since_timestamp=account.last_seen_timestamp,
                limit=50,
            )

            if not messages:
                logger.debug(f"No new messages for {account.email_address}")
                await update_account_status(account, success=True)
                return

            logger.info(f"Found {len(messages)} new messages for {account.email_address}")

            # Get user's rules
            rules = get_rules_for_account(account)

            # Process each message
            for msg in messages:
                await process_message(account, msg, rules, client)

            # Update last seen
            if messages:
                newest = messages[0]  # Assuming sorted by date desc
                await update_last_seen(account, newest.uid, newest.received_at)

            await update_account_status(account, success=True)

    except Exception as e:
        logger.error(f"Error checking {account.email_address}: {e}")
        await handle_account_error(account, str(e))
        raise


def get_rules_for_account(account: EmailAccount) -> list[EmailRule]:
    """Get rules applicable to this account."""
    with SessionLocal() as session:
        rules = (
            session.query(EmailRule)
            .filter(
                EmailRule.user_id == account.user_id,
                EmailRule.enabled == "true",
            )
            .filter(
                # Account-specific or global rules
                (EmailRule.account_id == account.id) | (EmailRule.account_id.is_(None))
            )
            .order_by(EmailRule.priority.desc())
            .all()
        )

        # Expunge from session
        for rule in rules:
            session.expunge(rule)

        return rules


async def process_message(
    account: EmailAccount,
    msg: EmailMessage,
    rules: list[EmailRule],
    client: Client,
) -> None:
    """Process a single email message."""
    # Check for duplicate alert
    if is_already_alerted(account.id, msg.uid):
        logger.debug(f"Already alerted for message {msg.uid}")
        return

    # Evaluate against rules
    match = evaluate_email(msg, rules)

    if not match:
        logger.debug(f"No rule match for message from {msg.from_addr}")
        return

    logger.info(f"Rule '{match.rule_name}' matched for message from {msg.from_addr}")

    # Check quiet hours
    if is_quiet_hours(account):
        logger.debug(f"Quiet hours active for {account.email_address}, skipping alert")
        return

    # Send Discord alert
    message_id = await send_email_alert(client, account, msg, match)

    # Record alert
    if message_id:
        record_alert(account, msg, match, message_id)


def is_already_alerted(account_id: str, email_uid: str) -> bool:
    """Check if we've already sent an alert for this email."""
    with SessionLocal() as session:
        existing = (
            session.query(EmailAlert)
            .filter(
                EmailAlert.account_id == account_id,
                EmailAlert.email_uid == email_uid,
            )
            .first()
        )
        return existing is not None


def is_quiet_hours(account: EmailAccount) -> bool:
    """Check if quiet hours are active for account."""
    if account.quiet_hours_start is None or account.quiet_hours_end is None:
        return False

    # Get current hour (simplified - ideally use user timezone)
    current_hour = datetime.now(UTC).hour

    start = account.quiet_hours_start
    end = account.quiet_hours_end

    if start < end:
        # Simple range (e.g., 23:00 - 07:00 doesn't wrap)
        return start <= current_hour < end
    else:
        # Wraps midnight (e.g., 23:00 - 07:00)
        return current_hour >= start or current_hour < end


async def send_email_alert(
    client: Client,
    account: EmailAccount,
    email: EmailMessage,
    match: RuleMatch,
) -> str | None:
    """Send formatted email alert to Discord channel."""
    if not account.alert_channel_id:
        logger.warning(f"No alert channel set for {account.email_address}")
        return None

    try:
        channel = client.get_channel(int(account.alert_channel_id))
        if not channel:
            logger.warning(f"Channel {account.alert_channel_id} not found")
            return None

        # Build embed
        color = {
            "urgent": 0xFF0000,  # Red
            "high": 0xFF9900,  # Orange
            "normal": 0x3498DB,  # Blue
            "low": 0x95A5A6,  # Gray
        }.get(match.importance, 0x3498DB)

        embed = discord.Embed(
            title=f"📬 {email.subject[:100]}",
            description=email.snippet[:200] if email.snippet else None,
            color=color,
            timestamp=email.received_at,
        )
        embed.add_field(name="From", value=email.from_addr[:100], inline=True)
        embed.add_field(name="Rule", value=match.rule_name, inline=True)
        embed.add_field(name="Account", value=account.email_address, inline=True)
        embed.set_footer(text=f"Importance: {match.importance.upper()}")

        # Determine if we should ping
        should_ping = False
        if match.override_ping == "true":
            should_ping = True
        elif match.override_ping == "false":
            should_ping = False
        elif account.ping_on_alert == "true":
            should_ping = True

        # Build mention
        mention = ""
        if should_ping:
            # Extract Discord user ID from user_id (e.g., "discord-123456")
            discord_user_id = account.user_id.replace("discord-", "")
            if discord_user_id.isdigit():
                mention = f"<@{discord_user_id}>"

        # Send
        msg = await channel.send(content=mention if mention else None, embed=embed)
        return str(msg.id)

    except Exception as e:
        logger.error(f"Failed to send email alert: {e}")
        return None


def record_alert(
    account: EmailAccount,
    email: EmailMessage,
    match: RuleMatch,
    message_id: str,
) -> None:
    """Record sent alert to database."""
    with SessionLocal() as session:
        alert = EmailAlert(
            user_id=account.user_id,
            account_id=account.id,
            rule_id=match.rule_id,
            email_uid=email.uid,
            email_from=email.from_addr[:255],
            email_subject=email.subject[:255],
            email_snippet=email.snippet[:500] if email.snippet else None,
            email_received_at=email.received_at,
            channel_id=account.alert_channel_id,
            message_id=message_id,
            importance=match.importance,
            was_pinged="true" if match.override_ping == "true" or account.ping_on_alert == "true" else "false",
        )
        session.add(alert)
        session.commit()


async def update_last_seen(account: EmailAccount, uid: str, timestamp: datetime) -> None:
    """Update account's last seen message info."""
    with SessionLocal() as session:
        acc = session.query(EmailAccount).filter(EmailAccount.id == account.id).first()
        if acc:
            acc.last_seen_uid = uid
            acc.last_seen_timestamp = timestamp
            session.commit()


async def update_account_status(account: EmailAccount, success: bool) -> None:
    """Update account status after check."""
    now = datetime.now(UTC).replace(tzinfo=None)

    with SessionLocal() as session:
        acc = session.query(EmailAccount).filter(EmailAccount.id == account.id).first()
        if acc:
            acc.last_checked_at = now

            if success:
                acc.status = "active"
                acc.error_count = 0
                acc.last_error = None
            else:
                acc.error_count = (acc.error_count or 0) + 1

            session.commit()

    # Schedule next check
    interval = account.poll_interval_minutes or EMAIL_DEFAULT_POLL_INTERVAL
    _next_checks[account.id] = now + timedelta(minutes=interval)


async def handle_account_error(account: EmailAccount, error: str) -> None:
    """Handle account check error with exponential backoff."""
    now = datetime.now(UTC).replace(tzinfo=None)

    with SessionLocal() as session:
        acc = session.query(EmailAccount).filter(EmailAccount.id == account.id).first()
        if acc:
            acc.last_checked_at = now
            acc.last_error = error[:500]
            acc.error_count = (acc.error_count or 0) + 1

            # Disable after too many errors
            if acc.error_count >= 10:
                acc.status = "error"
                logger.warning(f"Disabling account {acc.email_address} after {acc.error_count} errors")

            session.commit()

    # Exponential backoff for next check
    base_interval = account.poll_interval_minutes or EMAIL_DEFAULT_POLL_INTERVAL
    backoff = min(EMAIL_ERROR_BACKOFF_MAX, base_interval * (2 ** min(account.error_count or 0, 4)))
    _next_checks[account.id] = now + timedelta(minutes=backoff)
