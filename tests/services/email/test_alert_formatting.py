"""The email monitor must format alerts as plain markdown, with no discord import."""

import ast
import pathlib

import mypalclara.services.email.monitor as monitor


def test_monitor_module_does_not_import_discord():
    src = pathlib.Path(monitor.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "discord" not in imported


def test_format_email_alert_renders_markdown():
    text = monitor.format_email_alert(
        subject="Server down",
        from_addr="ops@example.com",
        account_email="me@example.com",
        rule_name="Urgent ops",
        importance="urgent",
        snippet="Disk at 99%",
    )
    assert "Server down" in text
    assert "ops@example.com" in text
    assert "Urgent ops" in text
    assert "URGENT" in text.upper()
