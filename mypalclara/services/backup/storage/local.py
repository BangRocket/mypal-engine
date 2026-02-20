"""Local filesystem storage backend."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from mypalclara.services.backup.storage import BackupEntry

if TYPE_CHECKING:
    from mypalclara.services.backup.config import BackupConfig

logger = logging.getLogger(__name__)

MARKER_FILE = ".last_backup"

# Map db_name to file extension
_EXTENSIONS: dict[str, str] = {
    "config": ".tar.gz",
}
_DEFAULT_EXT = ".sql.gz"

ALL_DB_NAMES = ["clara", "rook", "config"]


class LocalBackend:
    """Store backups on the local filesystem."""

    def __init__(self, config: BackupConfig) -> None:
        self.base_dir = config.local_backup_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _db_dir(self, db_name: str) -> Path:
        d = self.base_dir / db_name
        d.mkdir(parents=True, exist_ok=True)
        return d

    def upload(self, data: bytes, db_name: str, timestamp: str) -> str:
        d = self._db_dir(db_name)
        ext = _EXTENSIONS.get(db_name, _DEFAULT_EXT)
        filename = f"{db_name}_{timestamp}{ext}"
        path = d / filename
        path.write_bytes(data)
        logger.info(f"[{db_name}] Saved to {path}")
        return str(path)

    def download(self, key: str) -> bytes:
        path = Path(key)
        if not path.exists():
            raise FileNotFoundError(f"Backup not found: {key}")
        return path.read_bytes()

    def list_backups(self, db_name: str | None = None) -> list[BackupEntry]:
        entries: list[BackupEntry] = []
        db_names = [db_name] if db_name else ALL_DB_NAMES

        for name in db_names:
            db_dir = self.base_dir / name
            if not db_dir.exists():
                continue
            for f in db_dir.glob("*.gz"):
                stat = f.stat()
                entries.append(
                    BackupEntry(
                        key=str(f),
                        filename=f.name,
                        size=stat.st_size,
                        modified=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
                        db_name=name,
                    )
                )

        entries.sort(key=lambda e: e.modified, reverse=True)
        return entries

    def delete(self, key: str) -> None:
        path = Path(key)
        if path.exists():
            path.unlink()
            logger.info(f"Deleted {path}")

    def get_last_backup_time(self) -> datetime | None:
        marker = self.base_dir / MARKER_FILE
        if not marker.exists():
            return None
        try:
            data = json.loads(marker.read_text())
            return datetime.fromisoformat(data["timestamp"])
        except Exception:
            return None

    def set_last_backup_time(self, ts: datetime) -> None:
        marker = self.base_dir / MARKER_FILE
        marker.write_text(json.dumps({"timestamp": ts.isoformat()}))

    def cleanup_old(self, db_name: str, retention_days: int) -> int:
        cutoff = datetime.now(UTC) - timedelta(days=retention_days)
        deleted = 0
        db_dir = self.base_dir / db_name
        if not db_dir.exists():
            return 0

        for f in db_dir.glob("*.gz"):
            mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=UTC)
            if mtime < cutoff:
                f.unlink()
                logger.info(f"[{db_name}] Deleted old backup: {f.name}")
                deleted += 1

        return deleted
