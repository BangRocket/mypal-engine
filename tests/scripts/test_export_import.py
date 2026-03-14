"""Tests for the clara_export_import CLI."""

import json
import os
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

SCRIPT = str(Path(__file__).resolve().parent.parent.parent / "scripts" / "clara_export_import.py")

# Ensure repo root is on sys.path for direct imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))


def test_export_help():
    """export --help exits 0 and shows expected flags."""
    result = subprocess.run(
        [sys.executable, SCRIPT, "export", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "--user" in result.stdout
    assert "--since" in result.stdout
    assert "-o" in result.stdout


def test_import_help():
    """import --help exits 0 and shows expected flags."""
    result = subprocess.run(
        [sys.executable, SCRIPT, "import", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "--dry-run" in result.stdout
    assert "--re-embed" in result.stdout
    assert "--tables" in result.stdout


def test_build_manifest():
    """build_manifest returns all expected keys with correct values."""
    # Import the module directly to test build_manifest.
    import importlib.util

    spec = importlib.util.spec_from_file_location("clara_export_import", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    manifest = mod.build_manifest(
        source_backends={"sql": "sqlite", "vector": "qdrant"},
        filters={"user": "test-user"},
        record_counts={"messages": 42, "memories": 7},
    )

    assert manifest["version"] == "1"
    assert "created_at" in manifest
    assert manifest["source_backends"] == {"sql": "sqlite", "vector": "qdrant"}
    assert manifest["filters"] == {"user": "test-user"}
    assert manifest["embedding_model"] == "text-embedding-3-small"
    assert manifest["embedding_dimensions"] == 1536
    assert manifest["record_counts"] == {"messages": 42, "memories": 7}


def test_serialize_row():
    """serialize_row converts model instances to JSON-safe dicts."""
    from scripts.clara_export_import import serialize_row

    class FakeTable:
        columns = []

    class FakeCol:
        def __init__(self, name):
            self.name = name

    class FakeRow:
        __table__ = FakeTable()
        id = "test-1"
        name = "Test"
        created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        updated_at = None

    FakeRow.__table__.columns = [
        FakeCol("id"),
        FakeCol("name"),
        FakeCol("created_at"),
        FakeCol("updated_at"),
    ]

    result = serialize_row(FakeRow())
    assert result["id"] == "test-1"
    assert result["name"] == "Test"
    assert result["created_at"] == "2026-01-01T00:00:00+00:00"
    assert result["updated_at"] is None


def _subprocess_env(db_dir: Path) -> dict[str, str]:
    """Build a subprocess env dict pointing DATA_DIR at *db_dir*.

    ``connection.py`` builds a SQLite URL from ``DATA_DIR`` when
    ``DATABASE_URL`` is unset or not a PostgreSQL URL, so we control
    the database location via ``DATA_DIR`` instead.

    We set ``DATABASE_URL`` to an empty string (rather than removing
    it) so that ``load_dotenv(override=False)`` in ``connection.py``
    will not overwrite it with a PostgreSQL URL from the project's
    ``.env`` file.
    """
    env = dict(os.environ)
    env["DATA_DIR"] = str(db_dir)
    # Force SQLite fallback — empty string passes the `if DATABASE_URL`
    # check as falsy, so connection.py falls through to the SQLite path.
    # Setting it (instead of removing) prevents load_dotenv from
    # injecting a PostgreSQL URL from the project's .env file.
    env["DATABASE_URL"] = ""
    # Disable vector and graph backends
    env["QDRANT_URL"] = ""
    env["ROOK_DATABASE_URL"] = ""
    env["MEM0_DATABASE_URL"] = ""
    env["ENABLE_GRAPH_MEMORY"] = "false"
    return env


def test_full_roundtrip(tmp_path):
    """Full export -> clear -> import roundtrip preserves relational data."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from mypalclara.db.models import Base, Project

    # connection.py builds: sqlite:///{DATA_DIR}/assistant.db
    db_dir = tmp_path / "data"
    db_dir.mkdir()
    db_path = db_dir / "assistant.db"
    db_url = f"sqlite:///{db_path}"

    engine = create_engine(db_url)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)

    # Seed data
    db = Session()
    db.add(Project(id="p1", owner_id="u1", name="Test Project"))
    db.commit()
    db.close()

    env = _subprocess_env(db_dir)

    # Export
    result = subprocess.run(
        [sys.executable, SCRIPT, "export", "-o", str(tmp_path)],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, f"Export failed: {result.stderr}"

    # Verify archive exists and has manifest
    archives = list(tmp_path.glob("clara-export-*.tar.gz"))
    assert len(archives) == 1
    with tarfile.open(archives[0], "r:gz") as tar:
        manifest = json.load(tar.extractfile("manifest.json"))
        assert manifest["version"] == "1"
        assert manifest["record_counts"].get("projects", 0) >= 1

    # Clear the DB
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = Session()
    assert db.query(Project).count() == 0
    db.close()

    # Import
    result = subprocess.run(
        [sys.executable, SCRIPT, "import", str(archives[0])],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, f"Import failed: {result.stderr}"

    # Verify data restored
    db = Session()
    projects = db.query(Project).all()
    assert len(projects) == 1
    assert projects[0].id == "p1"
    assert projects[0].name == "Test Project"
    db.close()


def test_import_dry_run(tmp_path):
    """Dry run validates without writing."""
    db_dir = tmp_path / "drydata"
    db_dir.mkdir()
    env = _subprocess_env(db_dir)

    # Create minimal archive manually
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        (tmp_dir / "relational").mkdir()
        with open(tmp_dir / "relational" / "projects.jsonl", "w") as f:
            f.write(
                json.dumps(
                    {
                        "id": "p1",
                        "owner_id": "u1",
                        "name": "Dry",
                        "created_at": None,
                        "updated_at": None,
                    }
                )
                + "\n"
            )
        manifest = {
            "version": "1",
            "created_at": "2026-01-01",
            "source_backends": {},
            "filters": {},
            "embedding_model": "text-embedding-3-small",
            "embedding_dimensions": 1536,
            "record_counts": {"projects": 1},
        }
        with open(tmp_dir / "manifest.json", "w") as f:
            json.dump(manifest, f)
        archive = tmp_path / "dry.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            for item in tmp_dir.rglob("*"):
                if item.is_file():
                    tar.add(item, arcname=str(item.relative_to(tmp_dir)))

    result = subprocess.run(
        [sys.executable, SCRIPT, "import", str(archive), "--dry-run"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, f"Dry run failed: {result.stderr}"
    # "dry run" should appear somewhere in output
    combined = result.stdout.lower() + result.stderr.lower()
    assert "dry run" in combined
