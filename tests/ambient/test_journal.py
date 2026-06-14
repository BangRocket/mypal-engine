from datetime import datetime, timezone

from mypalclara.ambient import journal


def test_append_then_read_recent(tmp_path, monkeypatch):
    monkeypatch.setenv("AMBIENT_JOURNAL_DIR", str(tmp_path))
    import importlib
    importlib.reload(journal)

    now = datetime(2026, 6, 14, 12, 0, tzinfo=timezone.utc)
    journal.append_entry("discord-1", "Felt clearer about the plan today.", now=now)

    text = journal.read_recent("discord-1", days=1, now=now)
    assert "Felt clearer about the plan today." in text
    assert "2026-06-14" in text


def test_read_recent_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("AMBIENT_JOURNAL_DIR", str(tmp_path))
    import importlib
    importlib.reload(journal)
    assert journal.read_recent("nobody", days=3) == ""


def test_user_id_sanitized(tmp_path, monkeypatch):
    monkeypatch.setenv("AMBIENT_JOURNAL_DIR", str(tmp_path))
    import importlib
    importlib.reload(journal)
    p = journal.journal_path("discord/../evil", date="2026-06-14")
    assert ".." not in str(p.relative_to(tmp_path))
