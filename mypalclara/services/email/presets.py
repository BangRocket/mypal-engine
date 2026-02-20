"""Built-in rule presets for common email filtering scenarios."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from db.connection import SessionLocal
from db.models import EmailRule

if TYPE_CHECKING:
    pass

# =============================================================================
# Rule Presets
# =============================================================================

RULE_PRESETS = {
    "job_hunting": {
        "name": "Job Hunting",
        "description": "Alerts for job applications, interviews, and recruiter messages",
        "rule_definition": {
            "conditions": {
                "sender_domain": [
                    # ATS platforms
                    "greenhouse.io",
                    "lever.co",
                    "workday.com",
                    "icims.com",
                    "smartrecruiters.com",
                    "jobvite.com",
                    "taleo.net",
                    "successfactors.com",
                    "myworkdayjobs.com",
                    # Job boards
                    "linkedin.com",
                    "indeed.com",
                    "glassdoor.com",
                    "ziprecruiter.com",
                    "monster.com",
                    "hired.com",
                    "wellfound.com",  # AngelList
                    "ycombinator.com",
                ],
                "sender_contains": [
                    "recruiter",
                    "recruiting",
                    "talent",
                    "hiring",
                    "hr@",
                    "careers@",
                    "jobs@",
                ],
                "subject_contains": [
                    "interview",
                    "application",
                    "offer",
                    "next steps",
                    "phone screen",
                    "schedule",
                    "assessment",
                    "coding challenge",
                    "technical interview",
                    "onsite",
                    "following up",
                    "your candidacy",
                    "move forward",
                ],
            },
            "match_mode": "any",
        },
        "importance": "high",
    },
    "urgent": {
        "name": "Urgent Messages",
        "description": "Emails marked as important or containing urgent keywords",
        "rule_definition": {
            "conditions": {
                "subject_contains": [
                    "urgent",
                    "asap",
                    "immediately",
                    "time sensitive",
                    "action required",
                    "deadline",
                    "expiring",
                    "expires today",
                    "final notice",
                    "response needed",
                ],
                "subject_regex": r"(?i)\b(urgent|emergency|critical|important)\b",
            },
            "match_mode": "any",
        },
        "importance": "urgent",
    },
    "security": {
        "name": "Security Alerts",
        "description": "Password resets, security notifications, and 2FA codes",
        "rule_definition": {
            "conditions": {
                "subject_contains": [
                    "password reset",
                    "reset your password",
                    "security alert",
                    "sign-in attempt",
                    "new sign-in",
                    "verification code",
                    "2fa",
                    "two-factor",
                    "suspicious activity",
                    "unusual activity",
                    "account security",
                    "confirm your email",
                    "verify your account",
                ],
            },
            "match_mode": "any",
        },
        "importance": "high",
    },
    "financial": {
        "name": "Financial Alerts",
        "description": "Banking, payment, and financial notifications",
        "rule_definition": {
            "conditions": {
                "sender_domain": [
                    "paypal.com",
                    "venmo.com",
                    "chase.com",
                    "bankofamerica.com",
                    "wellsfargo.com",
                    "capitalone.com",
                    "discover.com",
                    "americanexpress.com",
                    "stripe.com",
                    "square.com",
                ],
                "subject_contains": [
                    "payment received",
                    "payment sent",
                    "transaction alert",
                    "fraud alert",
                    "suspicious transaction",
                    "low balance",
                    "statement ready",
                    "direct deposit",
                ],
            },
            "match_mode": "any",
        },
        "importance": "high",
    },
    "shipping": {
        "name": "Shipping & Delivery",
        "description": "Package tracking and delivery notifications",
        "rule_definition": {
            "conditions": {
                "sender_domain": [
                    "ups.com",
                    "fedex.com",
                    "usps.com",
                    "dhl.com",
                    "amazon.com",
                ],
                "subject_contains": [
                    "shipped",
                    "out for delivery",
                    "delivered",
                    "delivery attempt",
                    "tracking",
                    "your order",
                    "package",
                ],
            },
            "match_mode": "any",
        },
        "importance": "normal",
    },
}


def get_preset_names() -> list[str]:
    """Get list of available preset names."""
    return list(RULE_PRESETS.keys())


def get_preset_info(preset_name: str) -> dict | None:
    """Get preset info without creating a rule.

    Args:
        preset_name: Name of the preset

    Returns:
        Preset info dict or None if not found
    """
    preset = RULE_PRESETS.get(preset_name)
    if not preset:
        return None

    return {
        "name": preset["name"],
        "description": preset["description"],
        "importance": preset["importance"],
        "conditions": list(preset["rule_definition"]["conditions"].keys()),
    }


def list_presets() -> list[dict]:
    """Get info about all available presets."""
    return [
        {
            "id": key,
            "name": preset["name"],
            "description": preset["description"],
            "importance": preset["importance"],
        }
        for key, preset in RULE_PRESETS.items()
    ]


def apply_preset(
    user_id: str,
    preset_name: str,
    account_id: str | None = None,
) -> EmailRule | None:
    """Create a rule from a preset.

    Args:
        user_id: User to create rule for
        preset_name: Name of the preset to apply
        account_id: Optional specific account (null = all accounts)

    Returns:
        Created EmailRule or None if preset not found
    """
    preset = RULE_PRESETS.get(preset_name)
    if not preset:
        return None

    with SessionLocal() as session:
        # Check if preset already applied
        existing = (
            session.query(EmailRule)
            .filter(
                EmailRule.user_id == user_id,
                EmailRule.preset_name == preset_name,
                EmailRule.account_id == account_id,
            )
            .first()
        )

        if existing:
            # Already exists, return it
            return existing

        # Create new rule from preset
        rule = EmailRule(
            user_id=user_id,
            account_id=account_id,
            name=preset["name"],
            enabled="true",
            priority=10,  # Presets get priority 10 by default
            rule_definition=json.dumps(preset["rule_definition"]),
            importance=preset["importance"],
            preset_name=preset_name,
        )

        session.add(rule)
        session.commit()
        session.refresh(rule)

        return rule


def remove_preset(user_id: str, preset_name: str) -> bool:
    """Remove a preset rule.

    Args:
        user_id: User to remove preset from
        preset_name: Name of the preset to remove

    Returns:
        True if removed, False if not found
    """
    with SessionLocal() as session:
        rule = (
            session.query(EmailRule)
            .filter(
                EmailRule.user_id == user_id,
                EmailRule.preset_name == preset_name,
            )
            .first()
        )

        if not rule:
            return False

        session.delete(rule)
        session.commit()
        return True
