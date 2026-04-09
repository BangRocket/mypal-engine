"""Tool definitions for graph memory triple extraction."""

EXTRACT_TRIPLES_TOOL = {
    "type": "function",
    "function": {
        "name": "extract_triples",
        "description": (
            "Extract knowledge triples from text. Each triple represents a relationship "
            "between typed entities (person, project, place, concept, event). "
            "Use human-readable names (not platform IDs). Include temporal information when available."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "triples": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "subject": {
                                "type": "string",
                                "description": "Entity name — use real names, not IDs (e.g., 'Josh' not 'discord-123').",
                            },
                            "subject_type": {
                                "type": "string",
                                "enum": ["person", "project", "place", "concept", "event"],
                                "description": "Type of the subject entity.",
                            },
                            "predicate": {
                                "type": "string",
                                "description": "The relationship (e.g., 'parent_of', 'works_on', 'lives_in').",
                            },
                            "object": {
                                "type": "string",
                                "description": "Target entity name — use real names.",
                            },
                            "object_type": {
                                "type": "string",
                                "enum": ["person", "project", "place", "concept", "event"],
                                "description": "Type of the object entity.",
                            },
                            "temporal_note": {
                                "type": "string",
                                "description": "When this became true, if known (e.g., 'since 2025', 'born Oct 2025', 'currently').",
                            },
                        },
                        "required": ["subject", "subject_type", "predicate", "object", "object_type"],
                        "additionalProperties": False,
                    },
                    "description": "List of knowledge triples extracted from the text.",
                }
            },
            "required": ["triples"],
            "additionalProperties": False,
        },
    },
}
