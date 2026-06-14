import importlib


def test_defaults(monkeypatch):
    for var in [
        "AMBIENT_ENABLED",
        "AMBIENT_CRON",
        "AMBIENT_MIN_DM_GAP_HOURS",
        "AMBIENT_JOURNAL_READBACK_DAYS",
        "AMBIENT_RECENT_ACTIVITY_SKIP_MIN",
        "AMBIENT_ACTIVE_HOURS",
    ]:
        monkeypatch.delenv(var, raising=False)
    cfg = importlib.reload(importlib.import_module("mypalclara.ambient.config"))
    assert cfg.AMBIENT_ENABLED is False
    assert cfg.AMBIENT_CRON == "0 11,14,17,20 * * *"
    assert cfg.AMBIENT_MIN_DM_GAP_HOURS == 4.0
    assert cfg.AMBIENT_JOURNAL_READBACK_DAYS == 3
    assert cfg.AMBIENT_RECENT_ACTIVITY_SKIP_MIN == 15
    assert cfg.AMBIENT_ACTIVE_HOURS == "8-22"


def test_env_override(monkeypatch):
    monkeypatch.setenv("AMBIENT_ENABLED", "true")
    monkeypatch.setenv("AMBIENT_MIN_DM_GAP_HOURS", "2.5")
    cfg = importlib.reload(importlib.import_module("mypalclara.ambient.config"))
    assert cfg.AMBIENT_ENABLED is True
    assert cfg.AMBIENT_MIN_DM_GAP_HOURS == 2.5
