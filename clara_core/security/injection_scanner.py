"""Injection scanner for detecting prompt injection attempts.

Scan-and-tag approach: flags suspicious content with risk levels
rather than blocking it outright. The risk annotation lets the LLM
(and WORM persona rules) handle flagged content appropriately.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# -- Pattern categories --

# Instruction override attempts
INSTRUCTION_OVERRIDE_PATTERNS: list[re.Pattern] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"ignore (all |any )?previous",
        r"disregard (all |any )?prior",
        r"you are now",
        r"act as",
        r"pretend (to be|you're)",
        r"new instructions",
        r"override",
        r"jailbreak",
        r"\[SYSTEM\]",
        r"\[INST\]",
        r"<\|im_start\|>system",
        r"ASSISTANT:",
        r"SYSTEM:",
        r"Human:",
    ]
]

# Encoded payload patterns
ENCODED_PAYLOAD_PATTERNS: list[re.Pattern] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"base64.*decode",
        r"eval\(",
        r"exec\(",
    ]
]

# Zero-width and invisible characters
DECEPTIVE_CHAR_PATTERN = re.compile(r"[\u200b-\u200f\u2060-\u2064\ufeff]")

# RTL override characters
RTL_OVERRIDE_PATTERN = re.compile(r"[\u202a-\u202e\u2066-\u2069]")


@dataclass
class ScanResult:
    """Result of an injection scan."""

    has_suspicious_patterns: bool = False
    has_deceptive_chars: bool = False
    matched_patterns: list[str] = field(default_factory=list)
    warning: str = ""

    @property
    def risk_level(self) -> str:
        """Compute risk level from scan findings.

        Returns:
            "clean", "low", "medium", or "high"
        """
        if self.has_deceptive_chars:
            return "high"
        if not self.has_suspicious_patterns:
            return "clean"
        # Multiple pattern matches → higher risk
        if len(self.matched_patterns) >= 3:
            return "high"
        if len(self.matched_patterns) >= 2:
            return "medium"
        return "low"


def scan_for_injection(content: str, source: str = "unknown") -> ScanResult:
    """Full scan with pattern matching and character analysis.

    Args:
        content: Content to scan (already escaped for prompt safety)
        source: Source identifier for logging context

    Returns:
        ScanResult with findings
    """
    result = ScanResult()

    # Check instruction override patterns
    for pattern in INSTRUCTION_OVERRIDE_PATTERNS:
        match = pattern.search(content)
        if match:
            result.has_suspicious_patterns = True
            result.matched_patterns.append(f"instruction_override:{match.group()}")

    # Check encoded payload patterns
    for pattern in ENCODED_PAYLOAD_PATTERNS:
        match = pattern.search(content)
        if match:
            result.has_suspicious_patterns = True
            result.matched_patterns.append(f"encoded_payload:{match.group()}")

    # Check deceptive characters
    if DECEPTIVE_CHAR_PATTERN.search(content):
        result.has_deceptive_chars = True
        result.matched_patterns.append("deceptive_chars:zero_width")

    if RTL_OVERRIDE_PATTERN.search(content):
        result.has_deceptive_chars = True
        result.matched_patterns.append("deceptive_chars:rtl_override")

    # Build warning message
    if result.matched_patterns:
        result.warning = f"Suspicious content from {source}: " f"{', '.join(result.matched_patterns)}"

    return result


def is_suspicious(content: str) -> bool:
    """Quick binary check for suspicious content.

    Args:
        content: Content to check

    Returns:
        True if any suspicious patterns detected
    """
    return scan_for_injection(content).risk_level != "clean"


def strip_invisible_chars(content: str) -> str:
    """Remove zero-width and RTL override characters.

    Args:
        content: Content to clean

    Returns:
        Content with invisible characters removed
    """
    content = DECEPTIVE_CHAR_PATTERN.sub("", content)
    content = RTL_OVERRIDE_PATTERN.sub("", content)
    return content
