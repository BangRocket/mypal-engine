"""Database operations: dump, restore, connection checks."""

from __future__ import annotations

import gzip
import logging
import os
import subprocess
import time
from typing import TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    from backup_service.config import BackupConfig

logger = logging.getLogger(__name__)


def parse_db_url(url: str) -> dict:
    """Parse database URL into components."""
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)

    parsed = urlparse(url)
    return {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 5432,
        "user": parsed.username or "postgres",
        "password": parsed.password or "",
        "database": parsed.path.lstrip("/"),
    }


def mask_url(url: str) -> str:
    """Return a URL with the password masked for display."""
    parsed = urlparse(url)
    if parsed.password:
        masked = parsed._replace(netloc=f"{parsed.username}:****@{parsed.hostname}:{parsed.port or 5432}")
        return masked.geturl()
    return url


def check_db_connection(db_url: str, db_name: str, config: BackupConfig) -> bool:
    """Check if database is reachable with retry logic."""
    if not db_url:
        return False

    db = parse_db_url(db_url)
    env = os.environ.copy()
    env["PGPASSWORD"] = db["password"]

    for attempt in range(1, config.db_retry_attempts + 1):
        try:
            result = subprocess.run(
                [
                    "psql",
                    "-h",
                    db["host"],
                    "-p",
                    str(db["port"]),
                    "-U",
                    db["user"],
                    "-d",
                    db["database"],
                    "-c",
                    "SELECT 1",
                ],
                capture_output=True,
                env=env,
                timeout=10,
            )
            if result.returncode == 0:
                logger.info(f"[{db_name}] Database connection OK")
                return True
        except Exception as e:
            logger.warning(f"[{db_name}] Connection attempt {attempt} failed: {e}")

        if attempt < config.db_retry_attempts:
            delay = config.db_retry_delay * (2 ** (attempt - 1))
            logger.info(f"[{db_name}] Retrying in {delay}s...")
            time.sleep(delay)

    logger.error(f"[{db_name}] Failed to connect after {config.db_retry_attempts} attempts")
    return False


def dump_database(db_url: str, db_name: str, config: BackupConfig) -> tuple[bytes | None, int]:
    """Dump a PostgreSQL database using pg_dump.

    Returns (compressed_data, raw_size) or (None, 0) on failure.
    """
    if not db_url:
        logger.warning(f"[{db_name}] Skipped - no database URL configured")
        return None, 0

    db = parse_db_url(db_url)
    logger.info(f"[{db_name}] Starting dump of {db['database']}@{db['host']}")

    env = os.environ.copy()
    env["PGPASSWORD"] = db["password"]

    cmd = [
        "pg_dump",
        "-h",
        db["host"],
        "-p",
        str(db["port"]),
        "-U",
        db["user"],
        "-d",
        db["database"],
        "--format=plain",
        "--no-owner",
        "--no-acl",
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            env=env,
            timeout=config.dump_timeout,
        )

        if result.returncode != 0:
            logger.error(f"[{db_name}] pg_dump failed: {result.stderr.decode()[:500]}")
            return None, 0

        dump_data = result.stdout
        raw_size = len(dump_data)
        compressed = gzip.compress(dump_data, compresslevel=config.compression_level)

        ratio = (1 - len(compressed) / raw_size) * 100 if raw_size else 0
        logger.info(
            f"[{db_name}] Dump complete: {raw_size:,} bytes -> " f"{len(compressed):,} bytes ({ratio:.1f}% compression)"
        )
        return compressed, raw_size

    except FileNotFoundError:
        logger.error(f"[{db_name}] pg_dump not found - install postgresql-client")
        return None, 0
    except subprocess.TimeoutExpired:
        logger.error(f"[{db_name}] pg_dump timed out after {config.dump_timeout}s")
        return None, 0
    except Exception as e:
        logger.error(f"[{db_name}] Dump failed: {e}")
        return None, 0


def restore_database(db_url: str, backup_data: bytes, db_name: str) -> bool:
    """Restore a PostgreSQL database from gzipped SQL dump.

    Returns True on success.
    """
    db = parse_db_url(db_url)
    env = os.environ.copy()
    env["PGPASSWORD"] = db["password"]

    try:
        sql = gzip.decompress(backup_data)
    except Exception as e:
        logger.error(f"[{db_name}] Failed to decompress backup: {e}")
        return False

    # For rook/vectors DB, ensure pgvector extension exists
    if db_name == "rook":
        sql = b"CREATE EXTENSION IF NOT EXISTS vector;\n" + sql

    cmd = [
        "psql",
        "-h",
        db["host"],
        "-p",
        str(db["port"]),
        "-U",
        db["user"],
        "-d",
        db["database"],
    ]

    try:
        result = subprocess.run(
            cmd,
            input=sql,
            capture_output=True,
            env=env,
            timeout=600,
        )

        if result.returncode != 0:
            stderr = result.stderr.decode()[:500]
            logger.error(f"[{db_name}] psql restore failed: {stderr}")
            return False

        logger.info(f"[{db_name}] Restore completed successfully")
        return True

    except FileNotFoundError:
        logger.error(f"[{db_name}] psql not found - install postgresql-client")
        return False
    except subprocess.TimeoutExpired:
        logger.error(f"[{db_name}] Restore timed out after 600s")
        return False
    except Exception as e:
        logger.error(f"[{db_name}] Restore failed: {e}")
        return False
