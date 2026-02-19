"""Multi-layer contradiction detection for prediction error gating.

This module implements fast-to-slow contradiction detection layers:
1. Negation patterns: "likes X" vs "doesn't like X"
2. Antonym detection: "available" vs "busy"
3. Temporal conflicts: date/time mismatches
4. LLM semantic check: Full understanding (optional, expensive)

The goal is to detect when new information contradicts existing memories,
enabling smart memory updates (supersede vs update vs skip).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from clara_core.llm.messages import SystemMessage, UserMessage
from config.logging import get_logger

if TYPE_CHECKING:
    pass

logger = get_logger("contradiction")


class ContradictionType(str, Enum):
    """Types of contradictions detected."""

    NONE = "none"
    NEGATION = "negation"
    ANTONYM = "antonym"
    TEMPORAL = "temporal"
    SEMANTIC = "semantic"
    NUMERIC = "numeric"


@dataclass
class ContradictionResult:
    """Result of contradiction detection.

    Attributes:
        contradicts: Whether a contradiction was detected
        contradiction_type: Type of contradiction found
        confidence: Confidence in the detection (0-1)
        explanation: Human-readable explanation
        details: Additional details about the contradiction
    """

    contradicts: bool
    contradiction_type: ContradictionType = ContradictionType.NONE
    confidence: float = 0.0
    explanation: str = ""
    details: dict | None = None


# Common negation patterns
NEGATION_PATTERNS = [
    (r"\b(is|am|are|was|were)\b", r"\b(is|am|are|was|were)\s+(not|n't)\b"),
    (r"\b(do|does|did)\b", r"\b(do|does|did)\s+(not|n't)\b"),
    (r"\b(has|have|had)\b", r"\b(has|have|had)\s+(not|n't)\b"),
    (r"\b(can|could|will|would|should|might)\b", r"\b(can|could|will|would|should|might)\s+(not|n't)\b"),
    (r"\blikes?\b", r"\b(doesn't|does not|don't|do not)\s+like\b"),
    (r"\bloves?\b", r"\b(doesn't|does not|don't|do not)\s+love\b"),
    (r"\bwants?\b", r"\b(doesn't|does not|don't|do not)\s+want\b"),
    (r"\bworks?\b", r"\b(doesn't|does not|don't|do not)\s+work\b"),
    (r"\bprefers?\b", r"\b(doesn't|does not|don't|do not)\s+prefer\b"),
]

# Common antonym pairs
ANTONYM_PAIRS = [
    ("available", "busy"),
    ("available", "unavailable"),
    ("free", "busy"),
    ("happy", "sad"),
    ("happy", "unhappy"),
    ("good", "bad"),
    ("like", "dislike"),
    ("like", "hate"),
    ("love", "hate"),
    ("agree", "disagree"),
    ("want", "avoid"),
    ("prefer", "dislike"),
    ("enjoy", "dislike"),
    ("enjoy", "hate"),
    ("interested", "uninterested"),
    ("interested", "bored"),
    ("yes", "no"),
    ("true", "false"),
    ("correct", "incorrect"),
    ("right", "wrong"),
    ("active", "inactive"),
    ("enabled", "disabled"),
    ("on", "off"),
    ("open", "closed"),
    ("start", "end"),
    ("begin", "finish"),
    ("alive", "dead"),
    ("married", "single"),
    ("married", "divorced"),
    ("employed", "unemployed"),
    ("working", "retired"),
]

# Temporal patterns for date/time extraction
DATE_PATTERNS = [
    r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})\b",  # MM/DD/YYYY or DD/MM/YYYY
    r"\b(\d{4})[/-](\d{1,2})[/-](\d{1,2})\b",  # YYYY-MM-DD
    r"\b(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{1,2}),?\s*(\d{4})?\b",
    r"\b(\d{1,2})\s+(january|february|march|april|may|june|july|august|september|october|november|december),?\s*(\d{4})?\b",
]

TIME_PATTERNS = [
    r"\b(\d{1,2}):(\d{2})(?::(\d{2}))?\s*(am|pm)?\b",
    r"\b(\d{1,2})\s*(am|pm)\b",
]

# Numeric patterns
NUMERIC_PATTERNS = [
    r"\b(\d+(?:\.\d+)?)\s*(years?|months?|weeks?|days?|hours?|minutes?|seconds?)?\s+old\b",
    r"\b(\d+(?:\.\d+)?)\s*(years?|months?|weeks?|days?|hours?|minutes?|seconds?)\b",
    r"\$\s*(\d+(?:,\d{3})*(?:\.\d{2})?)\b",
    r"\b(\d+(?:,\d{3})*(?:\.\d{2})?)\s*(dollars?|USD|EUR|GBP|JPY)\b",
    r"\b(\d+(?:\.\d+)?)\s*%\b",
]


def detect_contradiction(
    new_content: str,
    existing_content: str,
    use_llm: bool = False,
    llm_callable: callable | None = None,
) -> ContradictionResult:
    """Detect if new content contradicts existing content.

    Uses a multi-layer approach, from fast to slow:
    1. Negation patterns (fastest)
    2. Antonym detection
    3. Temporal conflicts
    4. Numeric conflicts
    5. LLM semantic check (optional, slowest)

    Args:
        new_content: The new memory content
        existing_content: The existing memory content
        use_llm: Whether to use LLM for semantic checking
        llm_callable: Optional LLM function for semantic check

    Returns:
        ContradictionResult with detection details
    """
    # Normalize for comparison
    new_lower = new_content.lower().strip()
    existing_lower = existing_content.lower().strip()

    # Skip if identical
    if new_lower == existing_lower:
        return ContradictionResult(contradicts=False)

    # Layer 1: Negation patterns
    result = _check_negation_patterns(new_lower, existing_lower)
    if result.contradicts:
        return result

    # Layer 2: Antonym detection
    result = _check_antonyms(new_lower, existing_lower)
    if result.contradicts:
        return result

    # Layer 3: Temporal conflicts
    result = _check_temporal_conflicts(new_lower, existing_lower)
    if result.contradicts:
        return result

    # Layer 4: Numeric conflicts
    result = _check_numeric_conflicts(new_lower, existing_lower)
    if result.contradicts:
        return result

    # Layer 5: LLM semantic check (optional)
    if use_llm and llm_callable:
        result = _check_semantic_contradiction(new_content, existing_content, llm_callable)
        if result.contradicts:
            return result

    return ContradictionResult(contradicts=False)


def _check_negation_patterns(
    new_content: str,
    existing_content: str,
) -> ContradictionResult:
    """Check for negation pattern contradictions.

    Looks for patterns like:
    - "likes coffee" vs "doesn't like coffee"
    - "is available" vs "is not available"

    Args:
        new_content: New content (lowercase)
        existing_content: Existing content (lowercase)

    Returns:
        ContradictionResult
    """
    for positive_pattern, negative_pattern in NEGATION_PATTERNS:
        # Check if one has positive and other has negative
        new_has_positive = re.search(positive_pattern, new_content, re.IGNORECASE)
        new_has_negative = re.search(negative_pattern, new_content, re.IGNORECASE)
        existing_has_positive = re.search(positive_pattern, existing_content, re.IGNORECASE)
        existing_has_negative = re.search(negative_pattern, existing_content, re.IGNORECASE)

        # XOR: one positive, one negative
        if (new_has_positive and existing_has_negative) or (new_has_negative and existing_has_positive):
            # Extract the subject being negated for context
            return ContradictionResult(
                contradicts=True,
                contradiction_type=ContradictionType.NEGATION,
                confidence=0.8,
                explanation="Detected negation pattern contradiction",
                details={
                    "pattern": positive_pattern,
                    "new_has_negative": bool(new_has_negative),
                    "existing_has_negative": bool(existing_has_negative),
                },
            )

    return ContradictionResult(contradicts=False)


def _check_antonyms(
    new_content: str,
    existing_content: str,
) -> ContradictionResult:
    """Check for antonym contradictions.

    Looks for opposing words that suggest contradiction.

    Args:
        new_content: New content (lowercase)
        existing_content: Existing content (lowercase)

    Returns:
        ContradictionResult
    """
    # Extract words from both contents
    new_words = set(re.findall(r"\b\w+\b", new_content))
    existing_words = set(re.findall(r"\b\w+\b", existing_content))

    for word1, word2 in ANTONYM_PAIRS:
        # Check if antonyms are split between contents
        if (word1 in new_words and word2 in existing_words) or (word2 in new_words and word1 in existing_words):
            # Find common context words to verify they're about the same thing
            common_words = new_words & existing_words
            # Filter out very common words
            stop_words = {
                "the",
                "a",
                "an",
                "is",
                "are",
                "was",
                "were",
                "be",
                "been",
                "to",
                "of",
                "and",
                "or",
                "in",
                "on",
                "at",
                "for",
                "with",
                "that",
                "this",
                "it",
                "i",
                "you",
                "he",
                "she",
                "they",
                "we",
            }
            meaningful_common = common_words - stop_words

            if meaningful_common:
                return ContradictionResult(
                    contradicts=True,
                    contradiction_type=ContradictionType.ANTONYM,
                    confidence=0.7,
                    explanation=f"Antonym pair detected: '{word1}' vs '{word2}'",
                    details={
                        "antonym_pair": (word1, word2),
                        "common_context": list(meaningful_common)[:5],
                    },
                )

    return ContradictionResult(contradicts=False)


def _check_temporal_conflicts(
    new_content: str,
    existing_content: str,
) -> ContradictionResult:
    """Check for temporal/date conflicts.

    Looks for different dates or times referring to the same event.

    Args:
        new_content: New content (lowercase)
        existing_content: Existing content (lowercase)

    Returns:
        ContradictionResult
    """
    # Extract dates from both
    new_dates = []
    existing_dates = []

    for pattern in DATE_PATTERNS:
        new_dates.extend(re.findall(pattern, new_content, re.IGNORECASE))
        existing_dates.extend(re.findall(pattern, existing_content, re.IGNORECASE))

    # If both have dates and they differ, potential conflict
    if new_dates and existing_dates:
        # Normalize and compare (simplified)
        new_date_strs = {str(d) for d in new_dates}
        existing_date_strs = {str(d) for d in existing_dates}

        if new_date_strs != existing_date_strs and not new_date_strs & existing_date_strs:
            # Find common words to see if they're about the same thing
            new_words = set(re.findall(r"\b\w+\b", new_content))
            existing_words = set(re.findall(r"\b\w+\b", existing_content))
            common_words = new_words & existing_words
            stop_words = {
                "the",
                "a",
                "an",
                "is",
                "are",
                "was",
                "were",
                "be",
                "been",
                "to",
                "of",
                "and",
                "or",
                "in",
                "on",
                "at",
                "for",
                "with",
                "that",
                "this",
                "it",
            }
            meaningful_common = common_words - stop_words

            if meaningful_common:
                return ContradictionResult(
                    contradicts=True,
                    contradiction_type=ContradictionType.TEMPORAL,
                    confidence=0.6,
                    explanation="Different dates detected for potentially same event",
                    details={
                        "new_dates": list(new_date_strs),
                        "existing_dates": list(existing_date_strs),
                        "common_context": list(meaningful_common)[:5],
                    },
                )

    return ContradictionResult(contradicts=False)


def _check_numeric_conflicts(
    new_content: str,
    existing_content: str,
) -> ContradictionResult:
    """Check for numeric value conflicts.

    Looks for different numbers referring to the same property.

    Args:
        new_content: New content (lowercase)
        existing_content: Existing content (lowercase)

    Returns:
        ContradictionResult
    """
    for pattern in NUMERIC_PATTERNS:
        new_matches = re.findall(pattern, new_content, re.IGNORECASE)
        existing_matches = re.findall(pattern, existing_content, re.IGNORECASE)

        if new_matches and existing_matches:
            # Extract just the numeric values
            def extract_number(match):
                if isinstance(match, tuple):
                    return match[0]
                return match

            new_nums = {extract_number(m) for m in new_matches}
            existing_nums = {extract_number(m) for m in existing_matches}

            if new_nums != existing_nums and not new_nums & existing_nums:
                # Check for common context
                new_words = set(re.findall(r"\b\w+\b", new_content))
                existing_words = set(re.findall(r"\b\w+\b", existing_content))
                common_words = new_words & existing_words
                stop_words = {
                    "the",
                    "a",
                    "an",
                    "is",
                    "are",
                    "was",
                    "were",
                    "be",
                    "been",
                    "to",
                    "of",
                    "and",
                    "or",
                    "in",
                    "on",
                    "at",
                    "for",
                    "with",
                }
                meaningful_common = common_words - stop_words

                if meaningful_common:
                    return ContradictionResult(
                        contradicts=True,
                        contradiction_type=ContradictionType.NUMERIC,
                        confidence=0.65,
                        explanation="Different numeric values detected for potentially same property",
                        details={
                            "new_values": list(new_nums),
                            "existing_values": list(existing_nums),
                            "common_context": list(meaningful_common)[:5],
                        },
                    )

    return ContradictionResult(contradicts=False)


def _check_semantic_contradiction(
    new_content: str,
    existing_content: str,
    llm_callable: callable,
) -> ContradictionResult:
    """Use LLM to check for semantic contradictions.

    This is the most thorough but also most expensive check.
    Only use when other layers don't detect a contradiction but
    there's reason to believe one might exist.

    Args:
        new_content: New content (original case)
        existing_content: Existing content (original case)
        llm_callable: Function to call LLM

    Returns:
        ContradictionResult
    """
    prompt = f"""Analyze whether these two statements contradict each other.

Statement A (existing): {existing_content}
Statement B (new): {new_content}

Do these statements contradict each other? Consider:
- Direct contradictions (opposite facts)
- Implicit contradictions (incompatible implications)
- Temporal contradictions (different times for same event)

Respond with ONLY one of:
- CONTRADICT: [brief explanation]
- NO_CONTRADICTION: [brief explanation]
- UPDATES: [brief explanation] (if B provides new info that updates A without contradicting)
"""

    try:
        response = llm_callable(
            [
                SystemMessage(content="You are a logic analyzer that detects contradictions. Be precise and concise."),
                UserMessage(content=prompt),
            ]
        )

        response_upper = response.strip().upper()

        if response_upper.startswith("CONTRADICT"):
            explanation = response.split(":", 1)[1].strip() if ":" in response else "Semantic contradiction detected"
            return ContradictionResult(
                contradicts=True,
                contradiction_type=ContradictionType.SEMANTIC,
                confidence=0.85,
                explanation=explanation,
                details={"llm_response": response},
            )

    except Exception as e:
        logger.warning(f"LLM semantic check failed: {e}")

    return ContradictionResult(contradicts=False)


def calculate_similarity(text1: str, text2: str) -> float:
    """Calculate simple word-overlap similarity between two texts.

    This is a fast approximation used for prediction error gating.
    For high similarity (>0.95), consider skipping memory creation.

    Args:
        text1: First text
        text2: Second text

    Returns:
        Similarity score between 0 and 1
    """
    # Normalize
    words1 = set(re.findall(r"\b\w+\b", text1.lower()))
    words2 = set(re.findall(r"\b\w+\b", text2.lower()))

    if not words1 or not words2:
        return 0.0

    # Jaccard similarity
    intersection = len(words1 & words2)
    union = len(words1 | words2)

    return intersection / union if union > 0 else 0.0
