"""Utility functions for graph memory operations."""

import re

EXTRACT_TRIPLES_PROMPT = """You extract knowledge graph triples from conversation text.

Rules:
1. Extract only explicitly stated facts as (subject, predicate, object) triples.
2. For self-references ("I", "me", "my"), use "USER_ID" as the subject.
3. Use consistent, lowercase, general relationship names (e.g., "likes", "works_at", not "started_liking" or "currently_works_at").
4. Entity names should be natural and readable (e.g., "josh", "new york", not "josh_h_123").
5. Only extract facts that would be useful to remember in future conversations.
6. Do NOT extract trivial or transient information (greetings, acknowledgments, etc.).
"""


def format_entities(entities: list) -> str:
    """Format entities as 'source -- relationship -- destination' lines.

    Args:
        entities: List of entity dicts with source, relationship, destination keys

    Returns:
        Newline-separated formatted string
    """
    if not entities:
        return ""

    formatted_lines = []
    for entity in entities:
        simplified = f"{entity['source']} -- {entity['relationship']} -- {entity['destination']}"
        formatted_lines.append(simplified)

    return "\n".join(formatted_lines)


def sanitize_relationship_for_cypher(relationship: str) -> str:
    """Sanitize relationship text for Cypher queries.

    Args:
        relationship: Raw relationship string

    Returns:
        Sanitized relationship string safe for Cypher edge types
    """
    # Replace any non-alphanumeric/underscore with underscore
    sanitized = re.sub(r"[^a-zA-Z0-9_]", "_", relationship)
    # Collapse multiple underscores
    sanitized = re.sub(r"_+", "_", sanitized)
    return sanitized.strip("_").upper()
