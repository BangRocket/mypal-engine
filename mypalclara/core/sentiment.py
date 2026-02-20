"""Lightweight sentiment analysis for emotional continuity.

Uses VADER (Valence Aware Dictionary and sEntiment Reasoner) for fast,
rule-based sentiment analysis optimized for social media and conversational text.

VADER is particularly good at:
- Handling emoji and emoticons
- Understanding intensity modifiers ("very", "extremely")
- Recognizing slang and colloquialisms
- Processing negations ("not good")

Returns compound scores from -1 (most negative) to +1 (most positive).
"""

from __future__ import annotations

from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

# Lazy-loaded analyzer instance
_analyzer: SentimentIntensityAnalyzer | None = None


def get_analyzer() -> SentimentIntensityAnalyzer:
    """Get or create the VADER analyzer (lazy initialization)."""
    global _analyzer
    if _analyzer is None:
        _analyzer = SentimentIntensityAnalyzer()
    return _analyzer


def analyze_sentiment(text: str) -> dict[str, float]:
    """
    Analyze sentiment of text using VADER.

    Args:
        text: The text to analyze

    Returns:
        Dict with keys:
        - compound: Normalized score from -1 (negative) to +1 (positive)
        - positive: Proportion of positive sentiment (0-1)
        - negative: Proportion of negative sentiment (0-1)
        - neutral: Proportion of neutral sentiment (0-1)
    """
    if not text or not text.strip():
        return {
            "compound": 0.0,
            "positive": 0.0,
            "negative": 0.0,
            "neutral": 1.0,
        }

    scores = get_analyzer().polarity_scores(text)
    return {
        "compound": scores["compound"],
        "positive": scores["pos"],
        "negative": scores["neg"],
        "neutral": scores["neu"],
    }


def classify_sentiment(compound: float) -> str:
    """
    Classify a compound score into a category.

    Args:
        compound: VADER compound score (-1 to +1)

    Returns:
        One of: "positive", "negative", "neutral"
    """
    if compound >= 0.3:
        return "positive"
    elif compound <= -0.3:
        return "negative"
    else:
        return "neutral"


def get_sentiment_summary(text: str) -> tuple[float, str]:
    """
    Convenience function to get both compound score and classification.

    Args:
        text: The text to analyze

    Returns:
        Tuple of (compound_score, classification)
    """
    result = analyze_sentiment(text)
    compound = result["compound"]
    classification = classify_sentiment(compound)
    return compound, classification
