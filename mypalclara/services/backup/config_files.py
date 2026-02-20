"""Config file backup: tar.gz creation and extraction."""

from __future__ import annotations

import gzip
import io
import logging
import tarfile
from pathlib import Path

logger = logging.getLogger(__name__)


def dump_config_files(paths: list[str], compression_level: int = 9) -> tuple[bytes | None, int]:
    """Create an in-memory tar.gz of the given paths.

    Skips paths that don't exist. Directories are added recursively.
    Returns (compressed_data, raw_size) or (None, 0) if no files found.
    """
    buf = io.BytesIO()
    raw_size = 0
    files_added = 0

    try:
        with tarfile.open(fileobj=buf, mode="w") as tar:
            for path_str in paths:
                p = Path(path_str)
                if not p.exists():
                    logger.warning(f"[config] Skipping missing path: {path_str}")
                    continue

                arcname = p.name
                tar.add(str(p), arcname=arcname)

                if p.is_file():
                    raw_size += p.stat().st_size
                    files_added += 1
                elif p.is_dir():
                    for f in p.rglob("*"):
                        if f.is_file():
                            raw_size += f.stat().st_size
                            files_added += 1
    except Exception as e:
        logger.error(f"[config] Failed to create tar archive: {e}")
        return None, 0

    if files_added == 0:
        logger.warning("[config] No config files found to backup")
        return None, 0

    tar_data = buf.getvalue()
    compressed = gzip.compress(tar_data, compresslevel=compression_level)

    logger.info(f"[config] Archived {files_added} file(s): " f"{raw_size:,} bytes -> {len(compressed):,} bytes")
    return compressed, raw_size


def restore_config_files(backup_data: bytes, target_dir: Path) -> bool:
    """Extract a config tar.gz backup to the target directory.

    Returns True on success.
    """
    try:
        decompressed = gzip.decompress(backup_data)
    except Exception as e:
        logger.error(f"[config] Failed to decompress backup: {e}")
        return False

    try:
        buf = io.BytesIO(decompressed)
        with tarfile.open(fileobj=buf, mode="r") as tar:
            # Security: check for path traversal
            for member in tar.getmembers():
                member_path = Path(target_dir / member.name).resolve()
                if not str(member_path).startswith(str(target_dir.resolve())):
                    logger.error(f"[config] Blocked path traversal attempt: {member.name}")
                    return False

            tar.extractall(path=str(target_dir))

        logger.info(f"[config] Extracted config files to {target_dir}")
        return True

    except Exception as e:
        logger.error(f"[config] Failed to extract backup: {e}")
        return False
