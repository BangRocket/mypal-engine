"""Tool definitions for graph memory triple extraction."""

EXTRACT_TRIPLES_TOOL = {
    "type": "function",
    "function": {
        "name": "extract_triples",
        "description": "Extract knowledge triples (subject-predicate-object) from text. Each triple represents a fact or relationship.",
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
                                "description": "The entity that the fact is about (e.g., 'Josh', 'the project').",
                            },
                            "predicate": {
                                "type": "string",
                                "description": "The relationship or property (e.g., 'likes', 'works_at', 'lives_in').",
                            },
                            "object": {
                                "type": "string",
                                "description": "The value or target entity (e.g., 'pizza', 'Acme Corp', 'New York').",
                            },
                        },
                        "required": ["subject", "predicate", "object"],
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
