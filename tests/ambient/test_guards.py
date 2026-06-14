from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from mypalclara.ambient import guards
from mypalclara.db.models import Base, Session as DbSession


def test_in_active_hours_respects_timezone():
    # 12:00 UTC == 08:00 in New York (EDT, UTC-4) → inside 8-22
    now = datetime(2026, 6, 14, 12, 0, tzinfo=timezone.utc)
    assert guards.in_active_hours("America/New_York", now, "8-22") is True
    # 02:00 UTC == 22:00 prev day NY → outside 8-22
    night = datetime(2026, 6, 14, 2, 0, tzinfo=timezone.utc)
    assert guards.in_active_hours("America/New_York", night, "8-22") is False


def test_in_active_hours_bad_tz_falls_back_to_utc():
    now = datetime(2026, 6, 14, 10, 0, tzinfo=timezone.utc)
    assert guards.in_active_hours("Not/AZone", now, "8-22") is True


def test_past_min_gap():
    now = datetime(2026, 6, 14, 12, 0, tzinfo=timezone.utc).replace(tzinfo=None)
    assert guards.past_min_gap(None, now, 4) is True
    assert guards.past_min_gap(now - timedelta(hours=5), now, 4) is True
    assert guards.past_min_gap(now - timedelta(hours=1), now, 4) is False


def test_recently_active(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/s.db")
    Base.metadata.create_all(bind=engine)
    sf = sessionmaker(bind=engine)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    db = sf()
    db.add(DbSession(id="s1", project_id="p1", user_id="discord-1",
                     last_activity_at=now - timedelta(minutes=2)))
    db.commit()
    db.close()
    assert guards.recently_active("discord-1", 15, now=now, session_factory=sf) is True
    assert guards.recently_active("discord-2", 15, now=now, session_factory=sf) is False
