"""Tool definitions for graph memory entity extraction and relationship management."""

EXTRACT_ENTITIES_TOOL = {
    "type": "function",
    "function": {
        "name": "extract_entities",
        "description": "Extract entities and their types from the text.",
        "parameters": {
            "type": "object",
            "properties": {
                "entities": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "entity": {"type": "string", "description": "The name or identifier of the entity."},
                            "entity_type": {"type": "string", "description": "The type or category of the entity."},
                        },
                        "required": ["entity", "entity_type"],
                        "additionalProperties": False,
                    },
                    "description": "An array of entities with their types.",
                }
            },
            "required": ["entities"],
            "additionalProperties": False,
        },
    },
}

RELATIONS_TOOL = {
    "type": "function",
    "function": {
        "name": "establish_relationships",
        "description": "Establish relationships among the entities based on the provided text.",
        "parameters": {
            "type": "object",
            "properties": {
                "entities": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "source": {"type": "string", "description": "The source entity of the relationship."},
                            "relationship": {
                                "type": "string",
                                "description": "The relationship between the source and destination entities.",
                            },
                            "destination": {
                                "type": "string",
                                "description": "The destination entity of the relationship.",
                            },
                        },
                        "required": ["source", "relationship", "destination"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["entities"],
            "additionalProperties": False,
        },
    },
}

DELETE_MEMORY_TOOL_GRAPH = {
    "type": "function",
    "function": {
        "name": "delete_graph_memory",
        "description": "Delete the relationship between two nodes. This function deletes the existing relationship.",
        "parameters": {
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "description": "The identifier of the source node in the relationship.",
                },
                "relationship": {
                    "type": "string",
                    "description": "The existing relationship between the source and destination nodes that needs to be deleted.",
                },
                "destination": {
                    "type": "string",
                    "description": "The identifier of the destination node in the relationship.",
                },
            },
            "required": ["source", "relationship", "destination"],
            "additionalProperties": False,
        },
    },
}
