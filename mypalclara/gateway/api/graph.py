"""Graph entity/relationship query endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session as DBSession

from mypalclara.db.models import CanonicalUser
from mypalclara.db.user_identity import resolve_all_user_ids_for_canonical
from mypalclara.gateway.api.auth import get_approved_user, get_db

logger = logging.getLogger("gateway.api.graph")
router = APIRouter()


def _get_graph_store():
    """Get the graph store client (FalkorDB)."""
    import os

    provider = os.getenv("GRAPH_STORE_PROVIDER", "falkordb")
    if provider == "falkordb":
        try:
            from falkordb import FalkorDB

            host = os.getenv("FALKORDB_HOST", "localhost")
            port = int(os.getenv("FALKORDB_PORT", "6379"))
            password = os.getenv("FALKORDB_PASSWORD") or None
            graph_name = os.getenv("FALKORDB_GRAPH_NAME", "clara")

            db = FalkorDB(host=host, port=port, password=password)
            return db.select_graph(graph_name)
        except Exception as e:
            logger.error(f"Failed to connect to FalkorDB: {e}")
            raise HTTPException(status_code=503, detail="Graph store unavailable")
    raise HTTPException(status_code=501, detail=f"Unsupported graph provider: {provider}")


def _get_user_ids(user: CanonicalUser, db: DBSession) -> list[str]:
    """Get all prefixed user IDs for a canonical user."""
    return resolve_all_user_ids_for_canonical(user.id, db)


@router.get("/entities")
async def list_entities(
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    user: CanonicalUser = Depends(get_approved_user),
    db: DBSession = Depends(get_db),
):
    """List graph entities with pagination."""
    user_ids = _get_user_ids(user, db)
    if not user_ids:
        return {"entities": [], "total": 0}

    graph = _get_graph_store()
    try:
        # Query entities for user
        result = graph.query(
            "MATCH (n) WHERE n.user_id IN $user_ids RETURN n.name AS name, n.entity_type AS type, "
            "labels(n) AS labels SKIP $offset LIMIT $limit",
            {"user_ids": user_ids, "offset": offset, "limit": limit},
        )

        entities = []
        for row in result.result_set:
            entities.append({"name": row[0], "type": row[1], "labels": row[2]})

        # Count total
        count_result = graph.query(
            "MATCH (n) WHERE n.user_id IN $user_ids RETURN count(n) AS total",
            {"user_ids": user_ids},
        )
        total = count_result.result_set[0][0] if count_result.result_set else 0

        return {"entities": entities, "total": total, "offset": offset, "limit": limit}
    except Exception as e:
        logger.error(f"Graph query failed: {e}")
        raise HTTPException(status_code=500, detail="Graph query failed")


@router.get("/entities/{name}")
async def get_entity(
    name: str,
    user: CanonicalUser = Depends(get_approved_user),
    db: DBSession = Depends(get_db),
):
    """Get an entity with its relationships."""
    user_ids = _get_user_ids(user, db)
    graph = _get_graph_store()

    try:
        result = graph.query(
            "MATCH (n {name: $name})-[r]-(m) WHERE n.user_id IN $user_ids "
            "RETURN n.name AS source, type(r) AS rel_type, r.description AS rel_desc, "
            "m.name AS target, m.entity_type AS target_type",
            {"name": name, "user_ids": user_ids},
        )

        relationships = []
        for row in result.result_set:
            relationships.append(
                {
                    "source": row[0],
                    "relationship": row[1],
                    "description": row[2],
                    "target": row[3],
                    "target_type": row[4],
                }
            )

        return {"name": name, "relationships": relationships}
    except Exception as e:
        logger.error(f"Entity query failed: {e}")
        raise HTTPException(status_code=500, detail="Entity query failed")


@router.get("/search")
async def search_graph(
    q: str = Query(..., min_length=1),
    limit: int = Query(20, ge=1, le=100),
    user: CanonicalUser = Depends(get_approved_user),
    db: DBSession = Depends(get_db),
):
    """Search entities and relationships."""
    user_ids = _get_user_ids(user, db)
    graph = _get_graph_store()

    try:
        result = graph.query(
            "MATCH (n) WHERE n.user_id IN $user_ids AND toLower(n.name) CONTAINS toLower($query) "
            "RETURN n.name AS name, n.entity_type AS type LIMIT $limit",
            {"user_ids": user_ids, "query": q, "limit": limit},
        )

        entities = [{"name": row[0], "type": row[1]} for row in result.result_set]
        return {"results": entities}
    except Exception as e:
        logger.error(f"Graph search failed: {e}")
        raise HTTPException(status_code=500, detail="Graph search failed")


@router.get("/subgraph")
async def get_subgraph(
    center: str | None = Query(None, description="Center node name"),
    depth: int = Query(2, ge=1, le=5),
    limit: int = Query(100, ge=1, le=500),
    user: CanonicalUser = Depends(get_approved_user),
    db: DBSession = Depends(get_db),
):
    """Get a subgraph for visualization (nodes + edges)."""
    user_ids = _get_user_ids(user, db)
    graph = _get_graph_store()

    try:
        if center:
            result = graph.query(
                f"MATCH path = (n {{name: $center}})-[*1..{depth}]-(m) WHERE n.user_id IN $user_ids "
                "UNWIND relationships(path) AS r "
                "WITH startNode(r) AS s, endNode(r) AS e, type(r) AS rt "
                "RETURN DISTINCT s.name AS source, s.entity_type AS source_type, "
                "rt AS rel_type, e.name AS target, e.entity_type AS target_type "
                "LIMIT $limit",
                {"center": center, "user_ids": user_ids, "limit": limit},
            )
        else:
            result = graph.query(
                "MATCH (s)-[r]->(e) WHERE s.user_id IN $user_ids "
                "RETURN s.name AS source, s.entity_type AS source_type, "
                "type(r) AS rel_type, e.name AS target, e.entity_type AS target_type "
                "LIMIT $limit",
                {"user_ids": user_ids, "limit": limit},
            )

        nodes = {}
        edges = []
        for row in result.result_set:
            source, source_type, rel_type, target, target_type = row
            if source not in nodes:
                nodes[source] = {"id": source, "name": source, "type": source_type}
            if target not in nodes:
                nodes[target] = {"id": target, "name": target, "type": target_type}
            edges.append({"source": source, "target": target, "label": rel_type})

        return {"nodes": list(nodes.values()), "edges": edges}
    except Exception as e:
        logger.error(f"Subgraph query failed: {e}")
        raise HTTPException(status_code=500, detail="Subgraph query failed")
