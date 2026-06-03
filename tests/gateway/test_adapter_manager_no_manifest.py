"""adapter_manager must not import mypalclara.adapters (engine standalone)."""

import ast
import pathlib

import mypalclara.gateway.adapter_manager as am


def test_adapter_manager_does_not_import_adapters_package():
    src = pathlib.Path(am.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(a.name for a in node.names)
    offenders = {m for m in modules if m == "mypalclara.adapters" or m.startswith("mypalclara.adapters.")}
    assert not offenders, f"adapter_manager imports adapters: {offenders}"


def test_adapter_manager_loads_yaml_config(tmp_path):
    cfg = tmp_path / "adapters.yaml"
    cfg.write_text("adapters:\n" "  discord:\n" "    enabled: true\n" "    module: mypalclara.adapters.discord\n")
    mgr = am.AdapterManager(config_path=cfg)
    configs = mgr.load_config()
    assert "discord" in configs
    assert configs["discord"].module == "mypalclara.adapters.discord"
