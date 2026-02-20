"""Email importance rules engine.

Evaluates emails against user-defined rules to determine importance.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from config.logging import get_logger

if TYPE_CHECKING:
    from db.models import EmailRule
    from email_service.providers.base import EmailMessage

logger = get_logger("email.rules")


@dataclass
class RuleMatch:
    """Result of a rule match evaluation."""

    rule_id: str
    rule_name: str
    importance: str  # low, normal, high, urgent
    matched_conditions: list[str] = field(default_factory=list)
    custom_message: str | None = None
    override_ping: str | None = None  # "true", "false", or None (inherit)


def evaluate_email(
    email: EmailMessage,
    rules: list[EmailRule],
) -> RuleMatch | None:
    """Evaluate email against user's rules in priority order.

    Args:
        email: The email message to evaluate
        rules: List of rules sorted by priority (highest first)

    Returns:
        RuleMatch for first matching rule, or None if no match
    """
    # Sort by priority descending
    sorted_rules = sorted(rules, key=lambda r: r.priority, reverse=True)

    for rule in sorted_rules:
        if rule.enabled != "true":
            continue

        try:
            match = _evaluate_rule(email, rule)
            if match:
                return match
        except Exception as e:
            logger.error(f"Error evaluating rule {rule.id}: {e}")
            continue

    return None


def _evaluate_rule(email: EmailMessage, rule: EmailRule) -> RuleMatch | None:
    """Evaluate a single rule against an email."""
    try:
        definition = json.loads(rule.rule_definition)
    except json.JSONDecodeError:
        logger.error(f"Invalid rule definition JSON for rule {rule.id}")
        return None

    conditions = definition.get("conditions", {})
    match_mode = definition.get("match_mode", "any")  # "any" or "all"

    if not conditions:
        return None

    matched = []

    # Check each condition type
    if "sender_contains" in conditions:
        if _check_contains(email.from_addr, conditions["sender_contains"]):
            matched.append("sender_contains")

    if "sender_domain" in conditions:
        if _check_domain(email.from_addr, conditions["sender_domain"]):
            matched.append("sender_domain")

    if "subject_contains" in conditions:
        if _check_contains(email.subject, conditions["subject_contains"]):
            matched.append("subject_contains")

    if "subject_regex" in conditions:
        if _check_regex(email.subject, conditions["subject_regex"]):
            matched.append("subject_regex")

    if "body_contains" in conditions:
        if _check_contains(email.snippet, conditions["body_contains"]):
            matched.append("body_contains")

    if "has_attachments" in conditions:
        if email.has_attachments == conditions["has_attachments"]:
            matched.append("has_attachments")

    # Determine if rule matches based on match_mode
    if match_mode == "all":
        # All conditions must match
        all_conditions = len(conditions)
        if len(matched) < all_conditions:
            return None
    else:
        # Any condition must match (default)
        if not matched:
            return None

    return RuleMatch(
        rule_id=rule.id,
        rule_name=rule.name,
        importance=rule.importance or "normal",
        matched_conditions=matched,
        custom_message=rule.custom_alert_message,
        override_ping=rule.override_ping,
    )


def _check_contains(text: str, patterns: list[str]) -> bool:
    """Check if text contains any of the patterns (case-insensitive)."""
    if not text or not patterns:
        return False

    text_lower = text.lower()
    return any(pattern.lower() in text_lower for pattern in patterns)


def _check_domain(email_addr: str, domains: list[str]) -> bool:
    """Check if email is from any of the specified domains."""
    if not email_addr or not domains:
        return False

    # Extract domain from email address
    # Handle "Name <email@domain.com>" format
    match = re.search(r"@([\w.-]+)", email_addr)
    if not match:
        return False

    email_domain = match.group(1).lower()

    # Check if email domain matches or is subdomain of any target domain
    for domain in domains:
        domain_lower = domain.lower()
        if email_domain == domain_lower or email_domain.endswith(f".{domain_lower}"):
            return True

    return False


def _check_regex(text: str, pattern: str) -> bool:
    """Check if text matches regex pattern."""
    if not text or not pattern:
        return False

    try:
        return bool(re.search(pattern, text))
    except re.error as e:
        logger.warning(f"Invalid regex pattern '{pattern}': {e}")
        return False


def validate_rule_definition(definition: dict) -> tuple[bool, str | None]:
    """Validate a rule definition.

    Args:
        definition: Rule definition dict

    Returns:
        Tuple of (is_valid, error_message)
    """
    if not isinstance(definition, dict):
        return False, "Rule definition must be a dictionary"

    conditions = definition.get("conditions")
    if not conditions:
        return False, "Rule must have at least one condition"

    if not isinstance(conditions, dict):
        return False, "Conditions must be a dictionary"

    # Validate known condition types
    valid_conditions = {
        "sender_contains",
        "sender_domain",
        "subject_contains",
        "subject_regex",
        "body_contains",
        "has_attachments",
    }

    for key, value in conditions.items():
        if key not in valid_conditions:
            return False, f"Unknown condition type: {key}"

        if key == "has_attachments":
            if not isinstance(value, bool):
                return False, f"{key} must be a boolean"
        elif key == "subject_regex":
            if not isinstance(value, str):
                return False, f"{key} must be a string"
            try:
                re.compile(value)
            except re.error as e:
                return False, f"Invalid regex: {e}"
        else:
            if not isinstance(value, list):
                return False, f"{key} must be a list of strings"
            if not all(isinstance(v, str) for v in value):
                return False, f"All values in {key} must be strings"

    match_mode = definition.get("match_mode", "any")
    if match_mode not in ("any", "all"):
        return False, "match_mode must be 'any' or 'all'"

    return True, None
