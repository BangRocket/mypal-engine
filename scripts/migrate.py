#!/usr/bin/env python3
"""
Database migration management script.

Usage:
    poetry run python scripts/migrate.py                    # Run pending migrations
    poetry run python scripts/migrate.py status             # Show migration status
    poetry run python scripts/migrate.py create "message"   # Create new migration
    poetry run python scripts/migrate.py rollback           # Rollback one migration
    poetry run python scripts/migrate.py rollback 2         # Rollback 2 migrations
    poetry run python scripts/migrate.py stamp <revision>   # Mark revision as current (skip running)
    poetry run python scripts/migrate.py stamp head         # Mark head as current
    poetry run python scripts/migrate.py force-stamp        # Clear stale revision, stamp head
    poetry run python scripts/migrate.py force-stamp <rev>  # Clear stale revision, stamp specific
    poetry run python scripts/migrate.py heads              # Show current heads
    poetry run python scripts/migrate.py history            # Show migration history
    poetry run python scripts/migrate.py reset              # Reset to initial (DANGEROUS)
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text


def get_alembic_config() -> Config:
    """Get Alembic config, respecting DATABASE_URL."""
    project_root = Path(__file__).parent.parent
    alembic_cfg = Config(project_root / "alembic.ini")

    # Override with DATABASE_URL if set
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        # Fix postgres:// vs postgresql:// prefix
        if database_url.startswith("postgres://"):
            database_url = database_url.replace("postgres://", "postgresql://", 1)
        alembic_cfg.set_main_option("sqlalchemy.url", database_url)

    return alembic_cfg


def get_engine():
    """Get SQLAlchemy engine from config."""
    cfg = get_alembic_config()
    url = cfg.get_main_option("sqlalchemy.url")
    return create_engine(url)


def get_current_revision() -> str | None:
    """Get the current database revision."""
    engine = get_engine()
    with engine.connect() as conn:
        context = MigrationContext.configure(conn)
        return context.get_current_revision()


def get_head_revision() -> str | None:
    """Get the head revision from migration scripts.

    If multiple heads exist (branching), auto-merges them first.
    """
    cfg = get_alembic_config()
    script = ScriptDirectory.from_config(cfg)
    heads = script.get_heads()
    if len(heads) > 1:
        print(f"Multiple migration heads detected: {heads}")
        print("Auto-merging...")
        try:
            command.merge(cfg, list(heads), message="auto-merge migration heads")
            print("Heads merged successfully")
        except Exception as e:
            print(f"Auto-merge failed ({e}), upgrading to all heads")
            command.upgrade(cfg, "heads")
            return None
    return script.get_current_head()


def run_migrations():
    """Run all pending migrations."""
    cfg = get_alembic_config()
    current = get_current_revision()
    head = get_head_revision()

    if current == head:
        print(f"Database is up to date at revision: {current or 'base'}")
        return True

    print(f"Current revision: {current or 'base'}")
    print(f"Target revision:  {head}")
    print("Running migrations...")

    command.upgrade(cfg, "head")

    new_current = get_current_revision()
    print(f"Migrated to: {new_current}")
    return True


def show_status():
    """Show current migration status."""
    cfg = get_alembic_config()
    current = get_current_revision()
    head = get_head_revision()

    print("Migration Status")
    print("=" * 50)
    print(f"Database URL: {cfg.get_main_option('sqlalchemy.url')[:50]}...")
    print(f"Current revision: {current or '(none - database not initialized)'}")
    print(f"Head revision:    {head or '(no migrations)'}")

    if current == head:
        print("\nStatus: UP TO DATE")
    elif current is None:
        print("\nStatus: NOT INITIALIZED - run 'migrate' to initialize")
    else:
        print("\nStatus: PENDING MIGRATIONS")
        # Show pending migrations
        script = ScriptDirectory.from_config(cfg)
        for rev in script.iterate_revisions(head, current):
            print(f"  - {rev.revision}: {rev.doc}")


def create_migration(message: str):
    """Create a new migration with autogenerate."""
    cfg = get_alembic_config()
    print(f"Creating migration: {message}")
    command.revision(cfg, message=message, autogenerate=True)
    print("Migration created. Review the generated file before applying.")


def rollback(steps: int = 1):
    """Rollback migrations."""
    cfg = get_alembic_config()
    current = get_current_revision()

    if not current:
        print("No migrations to rollback")
        return

    if steps == 1:
        target = "-1"
    else:
        target = f"-{steps}"

    print(f"Rolling back {steps} migration(s) from {current}...")
    command.downgrade(cfg, target)

    new_current = get_current_revision()
    print(f"Rolled back to: {new_current or 'base'}")


def stamp_revision(revision: str):
    """Stamp the database with a revision without running migrations.

    Useful when tables already exist (e.g., created by create_all()).
    """
    cfg = get_alembic_config()
    current = get_current_revision()

    print(f"Current revision: {current or '(none)'}")
    print(f"Stamping as: {revision}")

    command.stamp(cfg, revision)

    new_current = get_current_revision()
    print(f"Database now at: {new_current}")


def force_stamp(revision: str = "head"):
    """Clear alembic_version table and stamp fresh.

    Use when the DB is stamped with a revision that doesn't exist in the
    current migration files (e.g., after switching branches).
    """
    engine = get_engine()
    with engine.connect() as conn:
        result = conn.execute(text("SELECT version_num FROM alembic_version"))
        old = [row[0] for row in result]
        if old:
            print(f"Clearing stale revision(s): {old}")
        conn.execute(text("DELETE FROM alembic_version"))
        conn.commit()

    cfg = get_alembic_config()
    print(f"Stamping as: {revision}")
    command.stamp(cfg, revision)

    new_current = get_current_revision()
    print(f"Database now at: {new_current}")


def show_heads():
    """Show current head revisions."""
    cfg = get_alembic_config()
    command.heads(cfg)


def show_history():
    """Show migration history."""
    cfg = get_alembic_config()
    command.history(cfg)


def reset_database():
    """Reset database to initial state (DANGEROUS!)."""
    response = input("WARNING: This will drop all tables! Type 'yes' to confirm: ")
    if response.lower() != "yes":
        print("Aborted")
        return

    cfg = get_alembic_config()
    print("Downgrading to base...")
    command.downgrade(cfg, "base")
    print("Database reset to initial state")


def main():
    parser = argparse.ArgumentParser(
        description="Database migration management",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="upgrade",
        choices=["upgrade", "status", "create", "rollback", "stamp", "force-stamp", "heads", "history", "reset"],
        help="Migration command (default: upgrade)",
    )
    parser.add_argument("args", nargs="*", help="Additional arguments (message for create, steps for rollback)")

    args = parser.parse_args()

    try:
        if args.command == "upgrade":
            run_migrations()
        elif args.command == "status":
            show_status()
        elif args.command == "create":
            if not args.args:
                print("Error: migration message required")
                print("Usage: migrate.py create 'add user table'")
                sys.exit(1)
            create_migration(" ".join(args.args))
        elif args.command == "rollback":
            steps = int(args.args[0]) if args.args else 1
            rollback(steps)
        elif args.command == "stamp":
            if not args.args:
                print("Error: revision required")
                print("Usage: migrate.py stamp <revision>")
                print("       migrate.py stamp head")
                sys.exit(1)
            stamp_revision(args.args[0])
        elif args.command == "force-stamp":
            revision = args.args[0] if args.args else "head"
            force_stamp(revision)
        elif args.command == "heads":
            show_heads()
        elif args.command == "history":
            show_history()
        elif args.command == "reset":
            reset_database()
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
