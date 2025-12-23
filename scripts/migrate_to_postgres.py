#!/usr/bin/env python3
"""
Migrate data from SQLite + Qdrant to PostgreSQL.

This script handles migration of:
1. SQLAlchemy models (Projects, Sessions, Messages, ChannelSummary) -> PostgreSQL
2. Qdrant vectors -> pgvector (via mem0)

Prerequisites:
1. Set up two PostgreSQL databases on Railway (or other provider)
2. Enable pgvector extension on the vectors database:
   CREATE EXTENSION IF NOT EXISTS vector;
3. Set environment variables:
   - DATABASE_URL: PostgreSQL connection string for main DB
   - MEM0_DATABASE_URL: PostgreSQL connection string for vectors DB

Usage:
    # Set environment variables first
    export DATABASE_URL=postgresql://user:pass@host:5432/clara_main
    export MEM0_DATABASE_URL=postgresql://user:pass@host:5432/clara_vectors

    # Run migration
    poetry run python scripts/migrate_to_postgres.py --sqlite
    poetry run python scripts/migrate_to_postgres.py --qdrant

    # Or run both
    poetry run python scripts/migrate_to_postgres.py --all
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()


def migrate_sqlite_to_postgres():
    """Migrate SQLAlchemy data from SQLite to PostgreSQL."""
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    # Check for required env vars
    postgres_url = os.getenv("DATABASE_URL")
    if not postgres_url:
        print("[ERROR] DATABASE_URL not set. Cannot migrate to PostgreSQL.")
        return False

    # Fix postgres:// prefix
    if postgres_url.startswith("postgres://"):
        postgres_url = postgres_url.replace("postgres://", "postgresql://", 1)

    # SQLite source
    data_dir = Path(os.getenv("DATA_DIR", "."))
    sqlite_path = data_dir / "assistant.db"

    if not sqlite_path.exists():
        print(f"[ERROR] SQLite database not found at {sqlite_path}")
        return False

    print(f"[migrate] Source: {sqlite_path}")
    print(f"[migrate] Target: {postgres_url.split('@')[1] if '@' in postgres_url else postgres_url}")

    # Connect to both databases
    sqlite_engine = create_engine(f"sqlite:///{sqlite_path}")
    postgres_engine = create_engine(postgres_url)

    SqliteSession = sessionmaker(bind=sqlite_engine)
    PostgresSession = sessionmaker(bind=postgres_engine)

    # Import models
    from db.models import Base, Project, Session, Message, ChannelSummary

    # Create tables in PostgreSQL
    print("[migrate] Creating tables in PostgreSQL...")
    Base.metadata.create_all(bind=postgres_engine)

    # Migrate data
    with SqliteSession() as sqlite_db, PostgresSession() as pg_db:
        # Projects
        projects = sqlite_db.query(Project).all()
        print(f"[migrate] Migrating {len(projects)} projects...")
        for p in projects:
            existing = pg_db.query(Project).filter(Project.id == p.id).first()
            if not existing:
                pg_db.add(
                    Project(id=p.id, owner_id=p.owner_id, name=p.name)
                )
        pg_db.commit()

        # Sessions
        sessions = sqlite_db.query(Session).all()
        print(f"[migrate] Migrating {len(sessions)} sessions...")
        for s in sessions:
            existing = pg_db.query(Session).filter(Session.id == s.id).first()
            if not existing:
                pg_db.add(
                    Session(
                        id=s.id,
                        project_id=s.project_id,
                        user_id=s.user_id,
                        title=s.title,
                        archived=s.archived,
                        started_at=s.started_at,
                        last_activity_at=s.last_activity_at,
                        previous_session_id=s.previous_session_id,
                        context_snapshot=s.context_snapshot,
                        session_summary=s.session_summary,
                    )
                )
        pg_db.commit()

        # Messages
        messages = sqlite_db.query(Message).all()
        print(f"[migrate] Migrating {len(messages)} messages...")
        for m in messages:
            existing = pg_db.query(Message).filter(Message.id == m.id).first()
            if not existing:
                pg_db.add(
                    Message(
                        id=m.id,
                        session_id=m.session_id,
                        user_id=m.user_id,
                        role=m.role,
                        content=m.content,
                        created_at=m.created_at,
                    )
                )
        pg_db.commit()

        # ChannelSummary
        summaries = sqlite_db.query(ChannelSummary).all()
        print(f"[migrate] Migrating {len(summaries)} channel summaries...")
        for cs in summaries:
            existing = pg_db.query(ChannelSummary).filter(ChannelSummary.id == cs.id).first()
            if not existing:
                pg_db.add(
                    ChannelSummary(
                        id=cs.id,
                        channel_id=cs.channel_id,
                        summary=cs.summary,
                        summary_cutoff_at=cs.summary_cutoff_at,
                        last_updated_at=cs.last_updated_at,
                    )
                )
        pg_db.commit()

    print("[migrate] SQLite -> PostgreSQL migration complete!")
    return True


def migrate_qdrant_to_pgvector():
    """
    Migrate vectors from Qdrant to pgvector.

    Note: mem0 handles vector storage internally, so we need to:
    1. Export memories from Qdrant-backed mem0
    2. Re-add them to pgvector-backed mem0
    """
    from qdrant_client import QdrantClient
    from mem0 import Memory

    # Check for required env vars
    pgvector_url = os.getenv("MEM0_DATABASE_URL")
    if not pgvector_url:
        print("[ERROR] MEM0_DATABASE_URL not set. Cannot migrate to pgvector.")
        return False

    # Fix postgres:// prefix
    if pgvector_url.startswith("postgres://"):
        pgvector_url = pgvector_url.replace("postgres://", "postgresql://", 1)

    # Qdrant source
    data_dir = Path(os.getenv("DATA_DIR", str(Path(__file__).parent.parent)))
    qdrant_path = data_dir / "qdrant_data"

    if not qdrant_path.exists():
        print(f"[ERROR] Qdrant data not found at {qdrant_path}")
        return False

    print(f"[migrate] Source: {qdrant_path}")
    print(f"[migrate] Target: {pgvector_url.split('@')[1] if '@' in pgvector_url else pgvector_url}")

    # Connect to Qdrant
    qdrant_client = QdrantClient(path=str(qdrant_path))

    # Check collection exists
    try:
        collection_info = qdrant_client.get_collection("mypalclara_memories")
        print(f"[migrate] Found {collection_info.points_count} vectors in Qdrant")
    except Exception as e:
        print(f"[ERROR] Could not read Qdrant collection: {e}")
        return False

    # Export all points from Qdrant
    print("[migrate] Exporting vectors from Qdrant...")
    points = []
    offset = None
    while True:
        result = qdrant_client.scroll(
            collection_name="mypalclara_memories",
            limit=100,
            offset=offset,
            with_vectors=True,
            with_payload=True,
        )
        batch_points, offset = result
        points.extend(batch_points)
        if offset is None:
            break

    print(f"[migrate] Exported {len(points)} vectors")

    if not points:
        print("[migrate] No vectors to migrate")
        return True

    # Save to JSON backup
    backup_file = data_dir / "qdrant_backup.json"
    backup_data = []
    for p in points:
        backup_data.append({
            "id": str(p.id),
            "vector": p.vector,
            "payload": p.payload,
        })
    with open(backup_file, "w") as f:
        json.dump(backup_data, f)
    print(f"[migrate] Backup saved to {backup_file}")

    # Initialize pgvector-backed mem0
    # Note: We need to import vectors directly into pgvector since mem0
    # doesn't expose a direct import API for raw vectors
    print("[migrate] Importing vectors to pgvector...")

    try:
        import psycopg2
        from pgvector.psycopg2 import register_vector

        conn = psycopg2.connect(pgvector_url)
        register_vector(conn)
        cur = conn.cursor()

        # Create table if not exists (matching mem0's schema)
        cur.execute("""
            CREATE EXTENSION IF NOT EXISTS vector;

            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                memory TEXT,
                hash TEXT,
                metadata_ JSONB,
                user_id TEXT,
                agent_id TEXT,
                run_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                embedding vector(1536)
            );

            CREATE INDEX IF NOT EXISTS memories_user_id_idx ON memories(user_id);
            CREATE INDEX IF NOT EXISTS memories_embedding_idx ON memories
                USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
        """)
        conn.commit()

        # Insert vectors
        imported = 0
        for point in points:
            payload = point.payload or {}
            memory_id = str(point.id)
            memory_text = payload.get("data", payload.get("memory", ""))
            user_id = payload.get("user_id", "")
            agent_id = payload.get("agent_id", "")
            run_id = payload.get("run_id", "")
            metadata = json.dumps({k: v for k, v in payload.items()
                                   if k not in ["data", "memory", "user_id", "agent_id", "run_id"]})
            vector = point.vector

            try:
                cur.execute("""
                    INSERT INTO memories (id, memory, metadata_, user_id, agent_id, run_id, embedding)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO NOTHING
                """, (memory_id, memory_text, metadata, user_id, agent_id, run_id, vector))
                imported += 1
            except Exception as e:
                print(f"[migrate] Error importing vector {memory_id}: {e}")

        conn.commit()
        cur.close()
        conn.close()

        print(f"[migrate] Imported {imported} vectors to pgvector")

    except ImportError:
        print("[ERROR] psycopg2 or pgvector not installed. Run: poetry install")
        return False
    except Exception as e:
        print(f"[ERROR] Failed to import to pgvector: {e}")
        return False

    print("[migrate] Qdrant -> pgvector migration complete!")
    return True


def main():
    parser = argparse.ArgumentParser(description="Migrate data to PostgreSQL")
    parser.add_argument("--sqlite", action="store_true", help="Migrate SQLite to PostgreSQL")
    parser.add_argument("--qdrant", action="store_true", help="Migrate Qdrant to pgvector")
    parser.add_argument("--all", action="store_true", help="Run all migrations")

    args = parser.parse_args()

    if not any([args.sqlite, args.qdrant, args.all]):
        parser.print_help()
        return

    success = True

    if args.sqlite or args.all:
        print("\n=== Migrating SQLite -> PostgreSQL ===\n")
        if not migrate_sqlite_to_postgres():
            success = False

    if args.qdrant or args.all:
        print("\n=== Migrating Qdrant -> pgvector ===\n")
        if not migrate_qdrant_to_pgvector():
            success = False

    if success:
        print("\n[migrate] All migrations completed successfully!")
    else:
        print("\n[migrate] Some migrations failed. Check errors above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
