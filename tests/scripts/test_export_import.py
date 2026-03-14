"""Tests for the clara_export_import CLI skeleton."""

import subprocess
import sys
from pathlib import Path

SCRIPT = str(Path(__file__).resolve().parent.parent.parent / "scripts" / "clara_export_import.py")


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
