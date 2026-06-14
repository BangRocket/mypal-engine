from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from mypalclara.ambient import inject, queue
from mypalclara.db.models import Base


def _factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/i.db")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)


def test_collect_formats_and_marks_delivered(tmp_path):
    sf = _factory(tmp_path)
    queue.enqueue("discord-1", "ask about the trip", session_factory=sf)
    queue.enqueue("discord-1", "the build is green now", session_factory=sf)

    block = inject.collect_surfaced_block("discord-1", session_factory=sf)
    assert "ask about the trip" in block
    assert "the build is green now" in block

    # second call returns empty — they were marked delivered
    assert inject.collect_surfaced_block("discord-1", session_factory=sf) == ""


def test_no_thoughts_returns_empty(tmp_path):
    sf = _factory(tmp_path)
    assert inject.collect_surfaced_block("discord-1", session_factory=sf) == ""
