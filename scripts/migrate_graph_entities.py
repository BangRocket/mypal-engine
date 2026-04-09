#!/usr/bin/env python3
"""Migrate graph entities from platform IDs to human-readable names.

Finds entities with names like "discord-271274659385835521" and renames
them to their resolved human name (e.g., "josh") using the entity resolver.

Usage:
    # Dry run — show what would change
    python scripts/migrate_graph_entities.py --dry-run

    # Full migration
    python scripts/migrate_graph_entities.py

    # Register a name mapping first, then migrate
    python scripts/migrate_graph_entities.py --register discord-271274659385835521=Josh
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

PLATFORM_RE = re.compile(r"^(discord|teams|slack|telegram|matrix|signal|whatsapp)-\d+$")


def main():
    parser = argparse.ArgumentParser(description="Migrate graph entities to human names")
    parser.add_argument("--dry-run", action="store_true", help="Show changes without applying")
    parser.add_argument("--register", action="append", default=[], help="Register name: id=Name")
    args = parser.parse_args()

    from mypalclara.core.memory.config import PALACE

    if PALACE is None:
        logger.error("Palace not initialized")
        sys.exit(1)

    if not hasattr(PALACE, "graph") or PALACE.graph is None:
        logger.error("Graph store not available")
        sys.exit(1)

    graph_store = PALACE.graph

    # Register any manually provided mappings
    from mypalclara.core.memory.entity_resolver import EntityResolver

    resolver = EntityResolver()

    for mapping in args.register:
        if "=" not in mapping:
            logger.warning(f"Invalid mapping format: {mapping} (use id=Name)")
            continue
        identifier, name = mapping.split("=", 1)
        resolver.register(identifier.strip(), name.strip(), source="manual")
        logger.info(f"Registered: {identifier} → {name}")

    # Set the resolver on the graph store
    graph_store._entity_resolver = resolver

    # Find all entities with platform-ID-style names
    try:
        results = graph_store._query("""
            MATCH (n:__Entity__)
            RETURN n.name AS name, n.user_id AS user_id, n.type AS type,
                   n.mentions AS mentions
            ORDER BY n.mentions DESC
        """)
    except Exception as e:
        logger.error(f"Failed to query entities: {e}")
        sys.exit(1)

    platform_entities = []
    for row in results:
        name = row.get("name", "")
        if PLATFORM_RE.match(name):
            resolved = resolver.resolve(name)
            if resolved != name:
                platform_entities.append({
                    "old_name": name,
                    "new_name": resolved.lower().replace(" ", "_"),
                    "user_id": row.get("user_id", ""),
                    "type": row.get("type", ""),
                    "mentions": row.get("mentions", 0),
                })

    logger.info(f"Found {len(platform_entities)} entities to rename")

    if not platform_entities:
        logger.info("No platform-ID entities found (or no name mappings registered)")
        return

    for ent in platform_entities:
        logger.info(f"  {ent['old_name']} → {ent['new_name']} (type={ent['type']}, mentions={ent['mentions']})")

    if args.dry_run:
        logger.info("Dry run — no changes made")
        return

    # Rename entities in the graph
    renamed = 0
    for ent in platform_entities:
        try:
            # Check if target name already exists — merge if so
            graph_store.graph.query(
                """
                MATCH (old:__Entity__ {name: $old_name, user_id: $user_id})
                OPTIONAL MATCH (existing:__Entity__ {name: $new_name, user_id: $user_id})
                WITH old, existing
                WHERE existing IS NULL
                SET old.name = $new_name, old.type = CASE WHEN old.type = 'concept' THEN 'person' ELSE old.type END
                """,
                params={
                    "old_name": ent["old_name"],
                    "new_name": ent["new_name"],
                    "user_id": ent["user_id"],
                },
            )
            renamed += 1
            logger.info(f"Renamed: {ent['old_name']} → {ent['new_name']}")
        except Exception as e:
            logger.error(f"Failed to rename {ent['old_name']}: {e}")

    logger.info(f"Renamed {renamed}/{len(platform_entities)} entities")


if __name__ == "__main__":
    main()
