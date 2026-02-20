"""S3-compatible storage backend (Wasabi, AWS, MinIO)."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import boto3
from botocore.exceptions import ClientError

from backup_service.storage import BackupEntry

if TYPE_CHECKING:
    from backup_service.config import BackupConfig

logger = logging.getLogger(__name__)

BACKUP_PREFIX = "backups"

# Map db_name to file extension
_EXTENSIONS: dict[str, str] = {
    "config": ".tar.gz",
}
_DEFAULT_EXT = ".sql.gz"


class S3Backend:
    """Store backups in S3-compatible object storage."""

    def __init__(self, config: BackupConfig) -> None:
        self.bucket = config.s3_bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=config.s3_endpoint_url,
            aws_access_key_id=config.s3_access_key,
            aws_secret_access_key=config.s3_secret_key,
            region_name=config.s3_region,
        )

    def verify(self) -> None:
        """Verify S3 credentials and bucket access. Raises on failure."""
        self.client.head_bucket(Bucket=self.bucket)

    def upload(self, data: bytes, db_name: str, timestamp: str) -> str:
        ext = _EXTENSIONS.get(db_name, _DEFAULT_EXT)
        key = f"{BACKUP_PREFIX}/{db_name}/{db_name}_{timestamp}{ext}"
        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=data,
            ContentType="application/gzip",
            Metadata={"backup-timestamp": timestamp, "database": db_name},
        )
        logger.info(f"[{db_name}] Uploaded to s3://{self.bucket}/{key}")
        return key

    def download(self, key: str) -> bytes:
        response = self.client.get_object(Bucket=self.bucket, Key=key)
        return response["Body"].read()

    def list_backups(self, db_name: str | None = None) -> list[BackupEntry]:
        entries: list[BackupEntry] = []

        if db_name:
            prefixes = [f"{BACKUP_PREFIX}/{db_name}/"]
            # Backward compat: also scan mem0/ prefix for old rook backups
            if db_name == "rook":
                prefixes.append(f"{BACKUP_PREFIX}/mem0/")
        else:
            prefixes = [
                f"{BACKUP_PREFIX}/clara/",
                f"{BACKUP_PREFIX}/rook/",
                f"{BACKUP_PREFIX}/mem0/",  # Backward compat
                f"{BACKUP_PREFIX}/config/",
            ]

        # Map prefix path component to display db_name
        _prefix_to_db = {
            "mem0": "rook",
            "clara": "clara",
            "rook": "rook",
            "config": "config",
        }

        for prefix in prefixes:
            # Determine db_name from prefix
            prefix_part = prefix.rstrip("/").split("/")[-1]
            entry_db = _prefix_to_db.get(prefix_part, db_name or "unknown")

            try:
                paginator = self.client.get_paginator("list_objects_v2")
                for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                    for obj in page.get("Contents", []):
                        filename = obj["Key"].split("/")[-1]
                        if not filename.endswith(".gz"):
                            continue
                        entries.append(
                            BackupEntry(
                                key=obj["Key"],
                                filename=filename,
                                size=obj["Size"],
                                modified=obj["LastModified"].replace(tzinfo=UTC),
                                db_name=entry_db,
                            )
                        )
            except ClientError as e:
                logger.warning(f"Failed to list {prefix}: {e}")

        entries.sort(key=lambda e: e.modified, reverse=True)
        return entries

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)
        logger.info(f"Deleted s3://{self.bucket}/{key}")

    def get_last_backup_time(self) -> datetime | None:
        marker_key = f"{BACKUP_PREFIX}/.last_backup"
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=marker_key)
            data = json.loads(response["Body"].read().decode())
            return datetime.fromisoformat(data["timestamp"])
        except ClientError:
            return None
        except Exception as e:
            logger.warning(f"Failed to read last backup marker: {e}")
            return None

    def set_last_backup_time(self, ts: datetime) -> None:
        marker_key = f"{BACKUP_PREFIX}/.last_backup"
        try:
            self.client.put_object(
                Bucket=self.bucket,
                Key=marker_key,
                Body=json.dumps({"timestamp": ts.isoformat()}).encode(),
                ContentType="application/json",
            )
        except Exception as e:
            logger.warning(f"Failed to write backup marker: {e}")

    def cleanup_old(self, db_name: str, retention_days: int) -> int:
        cutoff = datetime.now(UTC) - timedelta(days=retention_days)
        deleted = 0
        prefix = f"{BACKUP_PREFIX}/{db_name}/"

        try:
            paginator = self.client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    if obj["LastModified"].replace(tzinfo=UTC) < cutoff:
                        self.client.delete_object(Bucket=self.bucket, Key=obj["Key"])
                        logger.info(f"[{db_name}] Deleted old backup: {obj['Key']}")
                        deleted += 1
        except ClientError as e:
            logger.warning(f"[{db_name}] Cleanup failed: {e}")

        return deleted
