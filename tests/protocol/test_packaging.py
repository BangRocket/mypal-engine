"""mypal_protocol must be an importable, typed, standalone package."""

import pathlib

import mypal_protocol


def test_package_exposes_core_messages():
    assert hasattr(mypal_protocol, "RegisterMessage")
    assert hasattr(mypal_protocol, "RegisteredMessage")
    assert hasattr(mypal_protocol, "MessageType")


def test_package_has_distribution_metadata():
    root = pathlib.Path(mypal_protocol.__file__).resolve().parent
    assert (root / "pyproject.toml").exists(), "mypal_protocol must declare a pyproject.toml"
    assert (root / "py.typed").exists(), "mypal_protocol must ship a py.typed marker"


def test_package_is_self_contained():
    # The wire contract must not import engine/db code.
    import ast

    src = (pathlib.Path(mypal_protocol.__file__).resolve().parent / "messages.py").read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith("mypalclara"), f"protocol imports engine code: {node.module}"
