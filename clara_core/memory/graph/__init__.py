"""Graph memory implementations for Clara Memory System."""

from clara_core.memory.graph.factory import GraphStoreFactory


# Lazy imports to avoid requiring graph dependencies when not used
def get_neo4j_graph():
    """Get Neo4j MemoryGraph class (requires neo4j dependency)."""
    from clara_core.memory.graph.neo4j import MemoryGraph

    return MemoryGraph


def get_kuzu_graph():
    """Get Kuzu MemoryGraph class (requires kuzu dependency)."""
    from clara_core.memory.graph.kuzu import MemoryGraph

    return MemoryGraph


__all__ = [
    "GraphStoreFactory",
    "get_neo4j_graph",
    "get_kuzu_graph",
]
