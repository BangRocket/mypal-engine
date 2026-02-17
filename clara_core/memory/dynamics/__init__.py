"""Memory dynamics modules for Clara Memory System.

This module contains:
- FSRS-6 spaced repetition algorithm for memory scheduling
- Contradiction detection for smart memory updates
- Prediction error gating for memory decisions
"""

from clara_core.memory.dynamics.contradiction import (
    ContradictionResult,
    ContradictionType,
    calculate_similarity,
    detect_contradiction,
)
from clara_core.memory.dynamics.fsrs import (
    FsrsParams,
    Grade,
    MemoryState,
    ReviewResult,
    calculate_memory_score,
    infer_grade_from_signal,
    retrievability,
    review,
)

__all__ = [
    # FSRS
    "Grade",
    "FsrsParams",
    "MemoryState",
    "ReviewResult",
    "retrievability",
    "review",
    "infer_grade_from_signal",
    "calculate_memory_score",
    # Contradiction
    "ContradictionType",
    "ContradictionResult",
    "detect_contradiction",
    "calculate_similarity",
]
