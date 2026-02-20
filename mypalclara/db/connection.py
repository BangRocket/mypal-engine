from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool

load_dotenv()

logger = logging.getLogger("db")

# Support both SQLite (local dev) and PostgreSQL (production)
DATABASE_URL = os.getenv("DATABASE_URL")

if DATABASE_URL and DATABASE_URL.startswith("postgres"):
    # PostgreSQL with connection pooling
    # Railway and other hosts use postgresql:// prefix, SQLAlchemy prefers postgresql://
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

    engine = create_engine(
        DATABASE_URL,
        poolclass=QueuePool,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,  # Verify connections before use
        echo=False,
    )
    logger.info(f"Using PostgreSQL: {DATABASE_URL.split('@')[1] if '@' in DATABASE_URL else 'configured'}")
else:
    # Fallback to SQLite for local development
    DATA_DIR = Path(os.getenv("DATA_DIR", "."))
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DATABASE_URL = f"sqlite:///{DATA_DIR}/assistant.db"
    engine = create_engine(DATABASE_URL, echo=False, future=True)
    logger.info(f"Using SQLite: {DATABASE_URL}")

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_session():
    """Get a database session context manager."""
    return SessionLocal()


def init_db(run_migrations: bool = True) -> None:
    """Initialize the database.

    Args:
        run_migrations: If True, run Alembic migrations. If False, use create_all
                       (for testing or when migrations aren't available).
    """
    from mypalclara.db.models import Base

    if run_migrations:
        try:
            run_alembic_migrations()
        except Exception as e:
            logger.warning(f"Migration failed: {e}")

    # Always run create_all to ensure new tables exist
    # (create_all only creates tables that don't exist, it's safe to call after migrations)
    Base.metadata.create_all(bind=engine)


def run_alembic_migrations() -> None:
    """Run pending Alembic migrations."""
    from alembic import command
    from alembic.config import Config
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    # Find alembic.ini
    project_root = Path(__file__).parent.parent
    alembic_ini = project_root / "alembic.ini"

    if not alembic_ini.exists():
        logger.warning("alembic.ini not found, skipping migrations")
        return

    # Configure Alembic
    cfg = Config(str(alembic_ini))
    cfg.set_main_option("sqlalchemy.url", str(DATABASE_URL))

    # Check current state
    with engine.connect() as conn:
        context = MigrationContext.configure(conn)
        current_rev = context.get_current_revision()

    script = ScriptDirectory.from_config(cfg)

    # Handle multiple heads by auto-merging
    heads = script.get_heads()
    if len(heads) > 1:
        logger.warning(f"Multiple migration heads detected: {heads}. Auto-merging...")
        try:
            command.merge(cfg, list(heads), message="auto-merge migration heads")
            logger.info("Migration heads merged successfully")
        except Exception as e:
            logger.warning(f"Auto-merge failed ({e}), upgrading to all heads")
            command.upgrade(cfg, "heads")
            logger.info("Migrations complete (multi-head)")
            return

    head_rev = script.get_current_head()

    if current_rev == head_rev:
        logger.debug(f"Database up to date at {current_rev or 'base'}")
        return

    logger.info(f"Running migrations: {current_rev or 'base'} -> {head_rev}")
    command.upgrade(cfg, "head")
    logger.info("Migrations complete")


def get_engine():
    return engine
