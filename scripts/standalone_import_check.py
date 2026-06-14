#!/usr/bin/env python3
"""Standalone-load regression guard for the Band platform plugin.

Simulates how Hermes loads a *user-installed* plugin dropped into
``~/.hermes/plugins/band/`` -- imported as ``hermes_plugins.band`` from its own
package directory (mirroring ``hermes_cli/plugins.py::_load_directory_module``),
with no dependency on any in-repo ``plugins.platforms.band`` package path being
importable.

This is the exact context that pure-absolute self-imports
(``from plugins.platforms.band import tools``) silently break: a host repo / the
bundled test suite keeps passing because that path *is* importable there, so the
breakage only surfaces for a real community-plugin drop-in. Hence this guard
runs in CI.

Asserts:
  * the plugin module imports and exposes ``register()``
  * ``register()`` registers the ``band`` platform and at least one tool
  * NO ``plugins.platforms.band*`` module was imported -- i.e. the relative
    sibling imports resolved without falling back to any absolute repo path

Exit 0 on success, 1 on failure.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
import types

# scripts/ sits next to the package dir at the repo root.
REPO = pathlib.Path(__file__).resolve().parent.parent
PLUGIN_DIR = REPO / "hermes_band_platform"
NS = "hermes_plugins"


def main() -> int:
    init_file = PLUGIN_DIR / "__init__.py"
    if not init_file.exists():
        print(f"FAIL: {init_file} not found")
        return 1

    # Mirror _load_directory_module: a namespace parent package, then the plugin
    # loaded as ``hermes_plugins.band`` from its own directory.
    if NS not in sys.modules:
        ns = types.ModuleType(NS)
        ns.__path__ = []  # type: ignore[attr-defined]
        ns.__package__ = NS
        sys.modules[NS] = ns

    spec = importlib.util.spec_from_file_location(
        f"{NS}.band",
        init_file,
        submodule_search_locations=[str(PLUGIN_DIR)],
    )
    if spec is None or spec.loader is None:
        print("FAIL: could not create module spec for the plugin")
        return 1
    module = importlib.util.module_from_spec(spec)
    module.__package__ = f"{NS}.band"
    module.__path__ = [str(PLUGIN_DIR)]  # type: ignore[attr-defined]
    sys.modules[f"{NS}.band"] = module
    spec.loader.exec_module(module)  # raises if the relative imports are broken

    if not hasattr(module, "register"):
        print("FAIL: plugin module exposes no register()")
        return 1

    captured: dict = {"platform": None, "tools": []}

    class Ctx:
        def register_platform(self, **kwargs):
            captured["platform"] = kwargs.get("name")

        def register_tool(self, **kwargs):
            captured["tools"].append(kwargs.get("name"))

        def register_skill(self, *args, **kwargs):
            pass

    module.register(Ctx())  # raises if `from . import tools` cannot resolve

    # The absolute repo path must NOT have been imported. If it was, a sibling
    # import fell back to (or still uses) ``plugins.platforms.band.*`` -- which
    # works in a host repo but ImportErrors for a ~/.hermes/plugins/band drop-in.
    fallback_used = any(
        m == "plugins.platforms.band" or m.startswith("plugins.platforms.band.")
        for m in list(sys.modules)
    )

    print(
        f"platform={captured['platform']} "
        f"tools={len(captured['tools'])} "
        f"fallback_used={fallback_used}"
    )

    ok = (
        captured["platform"] == "band"
        and len(captured["tools"]) > 0
        and not fallback_used
    )
    if not ok:
        print("FAIL: standalone load did not behave as required")
        if fallback_used:
            print(
                "  -> a sibling import resolved via the absolute "
                "plugins.platforms.band path; a ~/.hermes/plugins/band drop-in "
                "would ImportError. Use relative imports (from . import ...)."
            )
        return 1

    print(
        "OK: Band plugin loads standalone as hermes_plugins.band "
        "(no plugins.platforms.band dependency)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
