"""Package and directory-plugin layout tests."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_root_manifest_matches_packaged_manifest():
    """Git directory installs and wheel installs expose the same manifest."""
    assert yaml.safe_load((ROOT / "plugin.yaml").read_text()) == yaml.safe_load(
        (ROOT / "hermes_band_platform" / "plugin.yaml").read_text()
    )


def test_root_directory_plugin_shim_registers():
    """The repository root loads as a Hermes directory plugin."""
    for key in list(sys.modules):
        if key == "hermes_plugins" or key.startswith("hermes_plugins.band"):
            sys.modules.pop(key)

    ns = types.ModuleType("hermes_plugins")
    ns.__path__ = []  # type: ignore[attr-defined]
    ns.__package__ = "hermes_plugins"
    sys.modules["hermes_plugins"] = ns

    spec = importlib.util.spec_from_file_location(
        "hermes_plugins.band",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    module.__package__ = "hermes_plugins.band"
    module.__path__ = [str(ROOT)]  # type: ignore[attr-defined]
    sys.modules["hermes_plugins.band"] = module
    spec.loader.exec_module(module)

    captured = {"platform": None, "tools": []}

    class Ctx:
        def register_platform(self, **kwargs):
            captured["platform"] = kwargs.get("name")

        def register_tool(self, **kwargs):
            captured["tools"].append(kwargs.get("name"))

        def register_skill(self, *args, **kwargs):
            pass

    module.register(Ctx())

    assert captured["platform"] == "band"
    assert "band_create_room" in captured["tools"]
