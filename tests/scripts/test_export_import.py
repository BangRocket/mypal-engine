"""Tests for the clara_export_import CLI."""

import subprocess
import sys
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
