"""Per-user dated reflection journal (markdown on disk)."""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _base_dir() -> Path:
    configured = os.getenv("AMBIENT_JOURNAL_DIR", "")
    if configured:
        return Path(configured)
    return Path(__file__).parent.parent / "ambient_journals"


def _sanitize_id(user_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", user_id)


def _today(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%d")


def journal_path(user_id: str, date: str | None = None) -> Path:
    date = date or _today()
    d = _base_dir() / _sanitize_id(user_id) / "journal"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{date}.md"


def append_entry(user_id: str, text: str, *, now: datetime | None = None) -> Path:
    now = now or datetime.now(timezone.utc)
    path = journal_path(user_id, _today(now))
    block = f"\n## {now.strftime('%H:%M UTC')}\n\n{text.strip()}\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(block)
    return path


def read_recent(user_id: str, days: int = 3, *, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    base = _base_dir() / _sanitize_id(user_id) / "journal"
    if not base.is_dir():
        return ""
    wanted = sorted((now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days))
    parts: list[str] = []
    for date in wanted:
        p = base / f"{date}.md"
        if p.exists():
            parts.append(f"# {date}\n{p.read_text(encoding='utf-8').strip()}")
    return "\n\n".join(parts)
