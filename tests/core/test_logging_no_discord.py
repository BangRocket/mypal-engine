"""config/logging.py must contain no `import discord` (engine is SDK-free)."""

import ast
import pathlib

import mypalclara.config.logging as clog


def test_logging_module_has_no_discord_import():
    src = pathlib.Path(clog.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(a.name.split(".")[0] != "discord" for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            assert node.module.split(".")[0] != "discord"
