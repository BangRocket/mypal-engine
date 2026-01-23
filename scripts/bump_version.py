#!/usr/bin/env python3
"""Bump version using CalVer format: YYYY.WW.N

Format: YYYY.WW.N where:
- YYYY = Year
- WW = ISO week number (01-53)
- N = Build number within the week (starts at 1)

Examples:
- 2026.04.1 = First build of week 4, 2026
- 2026.04.2 = Second build of week 4, 2026
- 2026.05.1 = First build of week 5, 2026

Usage:
    python scripts/bump_version.py          # Bump and show new version
    python scripts/bump_version.py --dry    # Show what would happen
    python scripts/bump_version.py --show   # Show current version only
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path

# Project root
ROOT = Path(__file__).parent.parent
VERSION_FILE = ROOT / "VERSION"
PYPROJECT_FILE = ROOT / "pyproject.toml"


def get_current_week_version() -> str:
    """Get the version string for the current week (build 1)."""
    today = date.today()
    year = today.year
    week = today.isocalendar()[1]
    return f"{year}.{week:02d}.1"


def parse_version(version: str) -> tuple[int, int, int]:
    """Parse YYYY.WW.N format into (year, week, build)."""
    match = re.match(r"(\d{4})\.(\d{2})\.(\d+)", version.strip())
    if not match:
        raise ValueError(f"Invalid version format: {version}")
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def bump_version(current: str) -> str:
    """Bump version: increment build if same week, otherwise start new week at 1."""
    today = date.today()
    current_year = today.year
    current_week = today.isocalendar()[1]

    try:
        year, week, build = parse_version(current)
    except ValueError:
        # Invalid format, start fresh
        return get_current_week_version()

    if year == current_year and week == current_week:
        # Same week, increment build
        return f"{year}.{week:02d}.{build + 1}"
    else:
        # New week (or year), start at 1
        return f"{current_year}.{current_week:02d}.1"


def read_version() -> str:
    """Read current version from VERSION file."""
    if VERSION_FILE.exists():
        return VERSION_FILE.read_text().strip()
    return get_current_week_version()


def write_version(version: str) -> None:
    """Write version to VERSION file and pyproject.toml."""
    # Update VERSION file
    VERSION_FILE.write_text(version + "\n")

    # Update pyproject.toml
    if PYPROJECT_FILE.exists():
        content = PYPROJECT_FILE.read_text()
        # Replace version = "x.y.z" with new version
        updated = re.sub(
            r'^version\s*=\s*"[^"]+"',
            f'version = "{version}"',
            content,
            flags=re.MULTILINE,
        )
        PYPROJECT_FILE.write_text(updated)


def main():
    parser = argparse.ArgumentParser(description="Bump CalVer version (YYYY.WW.N)")
    parser.add_argument("--dry", action="store_true", help="Show what would happen without changing files")
    parser.add_argument("--show", action="store_true", help="Show current version only")
    args = parser.parse_args()

    current = read_version()

    if args.show:
        print(current)
        return

    new = bump_version(current)

    if args.dry:
        print(f"Current: {current}")
        print(f"New:     {new}")
        return

    write_version(new)
    print(new)


if __name__ == "__main__":
    main()
