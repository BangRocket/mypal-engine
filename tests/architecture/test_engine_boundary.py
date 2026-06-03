"""Architecture boundary test for the gateway → mypal-engine extraction (Phase 1).

The engine packages in ENGINE_PACKAGES must never import a platform SDK
(discord, telethon, …) or the client-side `mypalclara.adapters` package. Known
remaining violations live in KNOWN_VIOLATIONS and shrink to empty as Phase 1
tasks land; the test fails if a NEW violation appears or if an allowlisted file
no longer violates (forcing the allowlist to stay in lockstep with the fixes).
"""

from __future__ import annotations

import ast
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PKG_ROOT = REPO_ROOT / "mypalclara"

# Directories that travel with the standalone engine.
ENGINE_PACKAGES = [
    "core",
    "db",
    "config",
    "sandbox",
    "tools",
    "gateway",
    "services/proactive",
    "services/blog",
    "services/email",
]

FORBIDDEN_SDK_ROOTS = {
    "discord",
    "telethon",
    "telegram",
    "slack_sdk",
    "slack_bolt",
    "botbuilder",
    "signalbot",
    "whatsapp",
}
FORBIDDEN_INTERNAL_PREFIXES = ("mypalclara.adapters",)

# Files (relative to mypalclara/) still expected to violate. Shrinks to {} by Task 8.
KNOWN_VIOLATIONS = {
    "services/proactive/engine.py",
    "config/logging.py",
    "gateway/adapter_manager.py",
}


def _iter_engine_files():
    for pkg in ENGINE_PACKAGES:
        base = PKG_ROOT / pkg
        if not base.exists():
            continue
        for path in base.rglob("*.py"):
            yield path


def _imported_modules(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None and node.level == 0:
                modules.add(node.module)
    return modules


def _is_forbidden(module: str) -> bool:
    top = module.split(".")[0]
    if top in FORBIDDEN_SDK_ROOTS:
        return True
    return any(module == p or module.startswith(p + ".") for p in FORBIDDEN_INTERNAL_PREFIXES)


def _current_violations() -> set[str]:
    violations: set[str] = set()
    for path in _iter_engine_files():
        rel = path.relative_to(PKG_ROOT).as_posix()
        if any(_is_forbidden(m) for m in _imported_modules(path)):
            violations.add(rel)
    return violations


def test_engine_has_no_new_client_or_sdk_imports():
    violations = _current_violations()
    unexpected = violations - KNOWN_VIOLATIONS
    assert not unexpected, f"New engine boundary violations: {sorted(unexpected)}"


def test_known_violations_allowlist_is_not_stale():
    violations = _current_violations()
    stale = KNOWN_VIOLATIONS - violations
    assert not stale, (
        "These files no longer violate — remove them from KNOWN_VIOLATIONS: "
        f"{sorted(stale)}"
    )


# --- Client side must not import gateway internals (only mypal_protocol / HTTP API) ---

CLIENT_PACKAGES = ["adapters"]

# Files (relative to mypalclara/) still importing mypalclara.gateway.*. Shrinks to {}.
KNOWN_CLIENT_GATEWAY_IMPORTS: set[str] = set()


def _client_gateway_importers() -> set[str]:
    found: set[str] = set()
    for pkg in CLIENT_PACKAGES:
        base = PKG_ROOT / pkg
        for path in base.rglob("*.py"):
            rel = path.relative_to(PKG_ROOT).as_posix()
            for module in _imported_modules(path):
                if module == "mypalclara.gateway" or module.startswith("mypalclara.gateway."):
                    found.add(rel)
                    break
    return found


def test_client_does_not_import_gateway_internals():
    importers = _client_gateway_importers()
    unexpected = importers - KNOWN_CLIENT_GATEWAY_IMPORTS
    assert not unexpected, f"Client modules importing gateway internals: {sorted(unexpected)}"
