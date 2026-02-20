"""Database backup service for Clara.

Provides backup functionality for Clara and Mem0 PostgreSQL databases
to S3-compatible storage (Wasabi, AWS S3, etc.).

This module is designed to be used directly by Clara's core and Discord commands,
not through an external API or MCP server.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import logging
import os
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


@dataclass
class BackupConfig:
    """Configuration for the backup service."""

    # Database URLs
    clara_db_url: str = ""
    mem0_db_url: str = ""

    # S3 configuration
    s3_bucket: str = "clara-backups"
    s3_endpoint_url: str = "https://s3.wasabisys.com"
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_region: str = "us-east-1"

    # Backup settings
    backup_prefix: str = "backups"
    retention_days: int = 7

    @classmethod
    def from_env(cls) -> BackupConfig:
        """Load configuration from environment variables."""
        return cls(
            clara_db_url=os.getenv("DATABASE_URL", ""),
            mem0_db_url=os.getenv("MEM0_DATABASE_URL", ""),
            s3_bucket=os.getenv("S3_BUCKET", "clara-backups"),
            s3_endpoint_url=os.getenv("S3_ENDPOINT_URL", "https://s3.wasabisys.com"),
            s3_access_key=os.getenv("S3_ACCESS_KEY", ""),
            s3_secret_key=os.getenv("S3_SECRET_KEY", ""),
            s3_region=os.getenv("S3_REGION", "us-east-1"),
            retention_days=int(os.getenv("BACKUP_RETENTION_DAYS", "7")),
        )


@dataclass
class BackupResult:
    """Result of a backup operation."""

    success: bool
    message: str
    databases_backed_up: list[str] = field(default_factory=list)
    databases_failed: list[str] = field(default_factory=list)
    databases_skipped: list[str] = field(default_factory=list)
    timestamp: str = ""
    errors: list[str] = field(default_factory=list)


@dataclass
class BackupEntry:
    """Information about a single backup file."""

    database: str
    filename: str
    size_bytes: int
    timestamp: datetime
    s3_key: str

    @property
    def size_mb(self) -> float:
        return self.size_bytes / (1024 * 1024)

    def to_dict(self) -> dict[str, Any]:
        return {
            "database": self.database,
            "filename": self.filename,
            "size_mb": round(self.size_mb, 2),
            "timestamp": self.timestamp.isoformat(),
            "s3_key": self.s3_key,
        }


class BackupService:
    """Service for managing database backups.

    Usage:
        service = BackupService()  # Uses env vars
        # or
        service = BackupService(config)  # Custom config

        # Run backup
        result = await service.backup_now()

        # List backups
        backups = await service.list_backups()

        # Get status
        status = await service.get_status()
    """

    def __init__(self, config: BackupConfig | None = None) -> None:
        """Initialize the backup service.

        Args:
            config: Optional configuration. If not provided, loads from environment.
        """
        self.config = config or BackupConfig.from_env()
        self._s3_client = None
        self._last_backup_time: datetime | None = None
        self._last_error: str | None = None

    def _get_s3_client(self):
        """Get or create the S3 client."""
        if self._s3_client is None:
            try:
                import boto3

                self._s3_client = boto3.client(
                    "s3",
                    endpoint_url=self.config.s3_endpoint_url,
                    aws_access_key_id=self.config.s3_access_key,
                    aws_secret_access_key=self.config.s3_secret_key,
                    region_name=self.config.s3_region,
                )
            except ImportError:
                raise RuntimeError("boto3 is required for backup functionality. Install with: pip install boto3")
        return self._s3_client

    def _parse_db_url(self, url: str) -> dict[str, Any]:
        """Parse database URL into components."""
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)

        parsed = urlparse(url)
        return {
            "host": parsed.hostname,
            "port": parsed.port or 5432,
            "user": parsed.username,
            "password": parsed.password,
            "database": parsed.path.lstrip("/"),
        }

    async def _check_db_connection(self, db_url: str, db_name: str) -> bool:
        """Check if database is reachable."""
        if not db_url:
            return False

        db = self._parse_db_url(db_url)
        env = os.environ.copy()
        env["PGPASSWORD"] = db["password"] or ""

        try:
            proc = await asyncio.create_subprocess_exec(
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
                env=env,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=10)
            return proc.returncode == 0
        except Exception as e:
            logger.warning(f"[{db_name}] Connection check failed: {e}")
            return False

    async def _dump_database(self, db_url: str, db_name: str) -> bytes | None:
        """Dump a PostgreSQL database using pg_dump."""
        if not db_url:
            logger.warning(f"[{db_name}] Skipped - no database URL configured")
            return None

        db = self._parse_db_url(db_url)
        logger.info(f"[{db_name}] Starting dump of {db['database']}@{db['host']}")

        env = os.environ.copy()
        env["PGPASSWORD"] = db["password"] or ""

        try:
            proc = await asyncio.create_subprocess_exec(
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
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=600)

            if proc.returncode != 0:
                logger.error(f"[{db_name}] pg_dump failed: {stderr.decode()[:500]}")
                return None

            compressed = gzip.compress(stdout, compresslevel=9)
            ratio = (1 - len(compressed) / len(stdout)) * 100 if stdout else 0
            logger.info(
                f"[{db_name}] Dump complete: {len(stdout):,} bytes -> "
                f"{len(compressed):,} bytes ({ratio:.1f}% compression)"
            )
            return compressed

        except FileNotFoundError:
            logger.error(f"[{db_name}] pg_dump not found - install postgresql-client")
            return None
        except asyncio.TimeoutError:
            logger.error(f"[{db_name}] pg_dump timed out after 10 minutes")
            return None
        except Exception as e:
            logger.error(f"[{db_name}] Dump failed: {e}")
            return None

    def _upload_backup(self, data: bytes, db_name: str, timestamp: str) -> bool:
        """Upload backup to S3."""
        key = f"{self.config.backup_prefix}/{db_name}/{db_name}_{timestamp}.sql.gz"
        s3 = self._get_s3_client()

        try:
            s3.put_object(
                Bucket=self.config.s3_bucket,
                Key=key,
                Body=data,
                ContentType="application/gzip",
                Metadata={
                    "backup-timestamp": timestamp,
                    "database": db_name,
                },
            )
            logger.info(f"[{db_name}] Uploaded to s3://{self.config.s3_bucket}/{key}")
            return True
        except Exception as e:
            logger.error(f"[{db_name}] Upload failed: {e}")
            return False

    def _cleanup_old_backups(self, db_name: str) -> int:
        """Remove backups older than retention period."""
        prefix = f"{self.config.backup_prefix}/{db_name}/"
        cutoff = datetime.now(UTC) - timedelta(days=self.config.retention_days)
        deleted = 0
        s3 = self._get_s3_client()

        try:
            paginator = s3.get_paginator("list_objects_v2")

            for page in paginator.paginate(Bucket=self.config.s3_bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    if obj["LastModified"].replace(tzinfo=UTC) < cutoff:
                        s3.delete_object(Bucket=self.config.s3_bucket, Key=obj["Key"])
                        logger.info(f"[{db_name}] Deleted old backup: {obj['Key']}")
                        deleted += 1

        except Exception as e:
            logger.warning(f"[{db_name}] Cleanup failed: {e}")

        return deleted

    def _validate_config(self) -> list[str]:
        """Validate the configuration and return a list of errors."""
        errors = []

        if not self.config.s3_access_key or not self.config.s3_secret_key:
            errors.append("S3 credentials not configured (S3_ACCESS_KEY, S3_SECRET_KEY)")

        if not self.config.clara_db_url and not self.config.mem0_db_url:
            errors.append("No database URLs configured (DATABASE_URL, MEM0_DATABASE_URL)")

        return errors

    def _check_s3_connection(self) -> bool:
        """Check if S3 is reachable."""
        try:
            s3 = self._get_s3_client()
            s3.head_bucket(Bucket=self.config.s3_bucket)
            return True
        except Exception as e:
            logger.error(f"S3 connection failed: {e}")
            return False

    async def backup_now(
        self,
        databases: list[str] | None = None,
    ) -> BackupResult:
        """Run an immediate backup.

        Args:
            databases: Optional list of databases to backup ("clara", "mem0").
                      If not specified, backs up all configured databases.

        Returns:
            BackupResult with details of the operation.
        """
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        result = BackupResult(success=False, message="", timestamp=timestamp)

        # Validate configuration
        errors = self._validate_config()
        if errors:
            result.errors = errors
            result.message = "; ".join(errors)
            return result

        # Check S3 connection
        if not self._check_s3_connection():
            result.errors.append("S3 connection failed")
            result.message = "S3 connection failed"
            return result

        # Determine which databases to backup
        db_configs = []
        if databases is None or "clara" in databases:
            if self.config.clara_db_url:
                db_configs.append(("clara", self.config.clara_db_url))
            elif databases and "clara" in databases:
                result.databases_skipped.append("clara (not configured)")

        if databases is None or "mem0" in databases:
            if self.config.mem0_db_url:
                db_configs.append(("mem0", self.config.mem0_db_url))
            elif databases and "mem0" in databases:
                result.databases_skipped.append("mem0 (not configured)")

        if not db_configs:
            result.message = "No databases to backup"
            return result

        # Run backups
        for db_name, db_url in db_configs:
            logger.info(f"Backing up {db_name}...")

            dump_data = await self._dump_database(db_url, db_name)
            if dump_data:
                if self._upload_backup(dump_data, db_name, timestamp):
                    result.databases_backed_up.append(db_name)
                    self._cleanup_old_backups(db_name)
                else:
                    result.databases_failed.append(db_name)
                    result.errors.append(f"{db_name}: upload failed")
            else:
                result.databases_failed.append(db_name)
                result.errors.append(f"{db_name}: dump failed")

        # Update state
        if result.databases_backed_up:
            self._last_backup_time = datetime.now(UTC)

        # Set result
        result.success = len(result.databases_failed) == 0 and len(result.databases_backed_up) > 0
        if result.success:
            result.message = f"Backup completed: {', '.join(result.databases_backed_up)}"
        elif result.databases_backed_up:
            result.message = f"Partial backup: {', '.join(result.databases_backed_up)} succeeded, {', '.join(result.databases_failed)} failed"
        else:
            result.message = f"Backup failed: {', '.join(result.errors)}"

        return result

    async def list_backups(
        self,
        database: str | None = None,
        limit: int = 10,
    ) -> list[BackupEntry]:
        """List available backups.

        Args:
            database: Optional filter by database name ("clara" or "mem0")
            limit: Maximum number of backups to return per database

        Returns:
            List of BackupEntry objects sorted by timestamp (newest first)
        """
        if not self._check_s3_connection():
            return []

        s3 = self._get_s3_client()
        backups = []

        db_names = [database] if database else ["clara", "mem0"]

        for db_name in db_names:
            prefix = f"{self.config.backup_prefix}/{db_name}/"

            try:
                response = s3.list_objects_v2(Bucket=self.config.s3_bucket, Prefix=prefix)

                for obj in response.get("Contents", []):
                    filename = obj["Key"].split("/")[-1]
                    if filename.endswith(".sql.gz"):
                        backups.append(
                            BackupEntry(
                                database=db_name,
                                filename=filename,
                                size_bytes=obj["Size"],
                                timestamp=obj["LastModified"].replace(tzinfo=UTC),
                                s3_key=obj["Key"],
                            )
                        )
            except Exception as e:
                logger.warning(f"Failed to list backups for {db_name}: {e}")

        # Sort by timestamp descending and limit
        backups.sort(key=lambda x: x.timestamp, reverse=True)
        return backups[:limit] if limit else backups

    async def get_status(self) -> dict[str, Any]:
        """Get current backup service status.

        Returns:
            Dictionary with status information
        """
        # Check configurations
        clara_configured = bool(self.config.clara_db_url)
        mem0_configured = bool(self.config.mem0_db_url)
        s3_configured = bool(self.config.s3_access_key and self.config.s3_secret_key)

        # Check connectivity
        s3_connected = self._check_s3_connection() if s3_configured else False

        clara_connected = False
        mem0_connected = False
        if clara_configured:
            clara_connected = await self._check_db_connection(self.config.clara_db_url, "clara")
        if mem0_configured:
            mem0_connected = await self._check_db_connection(self.config.mem0_db_url, "mem0")

        # Get last backup info
        last_backup = None
        if s3_connected:
            backups = await self.list_backups(limit=1)
            if backups:
                last_backup = backups[0].to_dict()

        return {
            "configured": {
                "clara_db": clara_configured,
                "mem0_db": mem0_configured,
                "s3": s3_configured,
            },
            "connected": {
                "clara_db": clara_connected,
                "mem0_db": mem0_connected,
                "s3": s3_connected,
            },
            "settings": {
                "s3_bucket": self.config.s3_bucket,
                "s3_endpoint": self.config.s3_endpoint_url,
                "retention_days": self.config.retention_days,
            },
            "last_backup": last_backup,
            "last_error": self._last_error,
        }


# Singleton instance
_backup_service: BackupService | None = None


def get_backup_service() -> BackupService:
    """Get the singleton backup service instance."""
    global _backup_service
    if _backup_service is None:
        _backup_service = BackupService()
    return _backup_service
