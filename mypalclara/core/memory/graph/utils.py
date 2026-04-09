"""Utility functions for graph memory operations."""

import re

EXTRACT_TRIPLES_PROMPT = """You extract knowledge graph triples from conversation text.

Rules:
1. Extract only explicitly stated facts as typed triples.
2. For self-references ("I", "me", "my"), use the user's real name if known, otherwise "user".
   Do NOT use platform IDs like "discord-271274659385835521" — use human names.
3. Assign entity types: person, project, place, concept, or event.
4. Use clear, meaningful relationship names (e.g., "parent_of", "works_on", "lives_in").
   Avoid vague predicates like "thinks_about" or "is_getting_fed_from".
5. Include temporal notes when the text indicates when something became true
   (e.g., "since 2025", "born Oct 2025", "currently", "recently started").
6. Only extract facts worth remembering in future conversations.
7. Do NOT extract trivial or transient information (greetings, debugging status, etc.).

Examples of GOOD triples:
- ("Josh", person) → parent_of → ("Anne", person), temporal: "born ~Oct 2025"
- ("Josh", person) → works_on → ("MyPalClara", project), temporal: "currently"
- ("Kinsey", person) → therapist_for → ("Josh", person)

Examples of BAD triples (too vague or transient):
- ("user", concept) → thinks_about → ("memory_system", concept)
- ("josh", person) → is_debugging → ("prod_issues", concept)
"""


def format_entities(entities: list) -> str:
    """Format entities as 'source -- relationship -- destination' lines."""
    if not entities:
        return ""

    formatted_lines = []
    for entity in entities:
        formatted_lines.append(
            f"{entity['source']} -- {entity['relationship']} -- {entity['destination']}"
        )

    return "\n".join(formatted_lines)


def sanitize_relationship_for_cypher(relationship: str) -> str:
    """Sanitize relationship text for Cypher queries."""
    sanitized = re.sub(r"[^a-zA-Z0-9_]", "_", relationship)
    sanitized = re.sub(r"_+", "_", sanitized)
    return sanitized.strip("_").upper()
