"""Tests for ambient DB models."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from mypalclara.db.models import AmbientUserConfig, Base, SurfacedThought


def _session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def test_surfaced_thought_roundtrip(tmp_path):
    db = _session(tmp_path)
    row = SurfacedThought(user_id="discord-1", content="hi", kind="queue")
    db.add(row)
    db.commit()
    got = db.query(SurfacedThought).filter_by(user_id="discord-1").one()
    assert got.content == "hi"
    assert got.kind == "queue"
    assert got.delivered == "false"  # string boolean default
    assert got.id  # gen_uuid populated


def test_ambient_user_config_defaults(tmp_path):
    db = _session(tmp_path)
    db.add(AmbientUserConfig(user_id="discord-1", timezone="America/New_York"))
    db.commit()
    got = db.query(AmbientUserConfig).filter_by(user_id="discord-1").one()
    assert got.reflection_opt_in == "false"
    assert got.timezone == "America/New_York"
    assert got.last_dm_at is None


def test_tables_present_in_metadata():
    assert "surfaced_thoughts" in Base.metadata.tables
    assert "ambient_user_config" in Base.metadata.tables
