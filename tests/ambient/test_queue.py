from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from mypalclara.ambient import queue
from mypalclara.db.models import Base


def _factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/q.db")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)


def test_enqueue_and_fetch(tmp_path):
    sf = _factory(tmp_path)
    queue.enqueue("discord-1", "thought one", kind="queue", session_factory=sf)
    rows = queue.fetch_undelivered("discord-1", session_factory=sf)
    assert len(rows) == 1
    assert rows[0].content == "thought one"


def test_expired_not_fetched(tmp_path):
    sf = _factory(tmp_path)
    past = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)
    queue.enqueue("discord-1", "stale", expires_at=past, session_factory=sf)
    assert queue.fetch_undelivered("discord-1", session_factory=sf) == []


def test_mark_delivered(tmp_path):
    sf = _factory(tmp_path)
    queue.enqueue("discord-1", "x", session_factory=sf)
    rows = queue.fetch_undelivered("discord-1", session_factory=sf)
    queue.mark_delivered([rows[0].id], session_factory=sf)
    assert queue.fetch_undelivered("discord-1", session_factory=sf) == []
