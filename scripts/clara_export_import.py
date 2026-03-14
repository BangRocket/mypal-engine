#!/usr/bin/env python3
"""
Export and import Clara's data (sessions, memories, vectors, graph).

Usage:
    python scripts/clara_export_import.py export -o ./backup --user josh
    python scripts/clara_export_import.py import ./backup.tar.gz --dry-run
"""

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

# Allow running from repo root without install.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

MANIFEST_VERSION = "1"

log = logging.getLogger("clara_export_import")


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


def build_manifest(
    *,
    source_backends: dict | None = None,
    filters: dict | None = None,
    record_counts: dict | None = None,
) -> dict:
    """Return a manifest dict describing the export archive."""
    return {
        "version": MANIFEST_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_backends": source_backends or {},
        "filters": filters or {},
        "embedding_model": "text-embedding-3-small",
        "embedding_dimensions": 1536,
        "record_counts": record_counts or {},
    }


# ---------------------------------------------------------------------------
# Subcommand stubs
# ---------------------------------------------------------------------------


def cmd_export(args: argparse.Namespace) -> None:
    """Export Clara data to a tar.gz archive."""
    log.info(
        "export requested  output=%s  user=%s  since=%s",
        args.output,
        args.user,
        args.since,
    )
    raise NotImplementedError("export is not yet implemented")


def cmd_import(args: argparse.Namespace) -> None:
    """Import Clara data from a tar.gz archive."""
    log.info(
        "import requested  archive=%s  dry_run=%s  re_embed=%s  tables=%s  strict=%s",
        args.archive,
        args.dry_run,
        args.re_embed,
        args.tables,
        args.strict,
    )
    raise NotImplementedError("import is not yet implemented")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Build and return the top-level argument parser."""
    parser = argparse.ArgumentParser(
        prog="clara_export_import",
        description="Export / import Clara data (sessions, memories, vectors, graph).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # -- export ---------------------------------------------------------------
    p_export = sub.add_parser("export", help="Export data to a tar.gz archive")
    p_export.add_argument(
        "-o",
        "--output",
        default=".",
        help="Directory to write the archive into (default: current dir)",
    )
    p_export.add_argument("--user", default=None, help="Export only this user's data")
    p_export.add_argument(
        "--since",
        default=None,
        help="Only include records created/updated after this ISO date",
    )
    p_export.set_defaults(func=cmd_export)

    # -- import ---------------------------------------------------------------
    p_import = sub.add_parser("import", help="Import data from a tar.gz archive")
    p_import.add_argument("archive", help="Path to the .tar.gz archive to import")
    p_import.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and report without writing",
    )
    p_import.add_argument(
        "--re-embed",
        action="store_true",
        help="Recompute embeddings instead of importing stored vectors",
    )
    p_import.add_argument(
        "--tables",
        default=None,
        help="Comma-separated list of tables to import (default: all)",
    )
    p_import.add_argument(
        "--strict",
        action="store_true",
        help="Abort on first error instead of skipping bad records",
    )
    p_import.set_defaults(func=cmd_import)

    return parser


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-5s %(message)s",
    )
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
