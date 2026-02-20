"""Storage backends for backup service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from backup_service.config import BackupConfig


@dataclass
class BackupEntry:
    """Metadata for a single backup file."""

    key: str  # Full path/key
    filename: str  # Just the filename
    size: int  # Bytes
    modified: datetime
    db_name: str  # "clara", "rook", or "config"


class StorageBackend(Protocol):
    """Protocol for backup storage backends."""

    def upload(self, data: bytes, db_name: str, timestamp: str) -> str: ...

    def download(self, key: str) -> bytes: ...

    def list_backups(self, db_name: str | None = None) -> list[BackupEntry]: ...

    def delete(self, key: str) -> None: ...

    def get_last_backup_time(self) -> datetime | None: ...

    def set_last_backup_time(self, ts: datetime) -> None: ...

    def cleanup_old(self, db_name: str, retention_days: int) -> int: ...


def create_backend(config: BackupConfig) -> StorageBackend:
    """Create a storage backend based on configuration."""
    if config.storage_type == "s3":
        from backup_service.storage.s3 import S3Backend

        return S3Backend(config)

    from backup_service.storage.local import LocalBackend

    return LocalBackend(config)
