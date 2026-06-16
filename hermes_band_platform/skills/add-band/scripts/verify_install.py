#!/usr/bin/env python3
"""Verify that the Band Hermes plugin is installed, enabled, and configured."""

from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any


def _entry_points_for_group() -> list[Any]:
    eps = importlib.metadata.entry_points()
    if hasattr(eps, "select"):
        return list(eps.select(group="hermes_agent.plugins"))
    if isinstance(eps, dict):
        return list(eps.get("hermes_agent.plugins", []))
    return [ep for ep in eps if ep.group == "hermes_agent.plugins"]


def _has_band_entry_point() -> bool:
    try:
        return any(ep.name == "band" for ep in _entry_points_for_group())
    except Exception:
        return False


def _has_directory_manifest() -> bool:
    root = Path(__file__).resolve().parents[4]
    return (root / "plugin.yaml").exists() and (root / "__init__.py").exists()


def _plugin_enabled() -> bool:
    try:
        from hermes_cli.config import load_config

        config = load_config()
    except Exception:
        return False
    plugins_cfg = config.get("plugins", {}) if isinstance(config, dict) else {}
    enabled = plugins_cfg.get("enabled", []) if isinstance(plugins_cfg, dict) else []
    return isinstance(enabled, list) and "band" in enabled


def _env_value(name: str) -> str:
    try:
        from hermes_cli.config import get_env_value

        return str(get_env_value(name) or "")
    except Exception:
        return os.getenv(name, "")


def verify_install() -> dict[str, Any]:
    package_importable = importlib.util.find_spec("hermes_band_platform") is not None
    sdk_importable = importlib.util.find_spec("band") is not None
    entry_point = _has_band_entry_point()
    directory_manifest = _has_directory_manifest()
    enabled = _plugin_enabled()
    agent_id_present = bool(_env_value("BAND_AGENT_ID").strip())
    api_key_present = bool(_env_value("BAND_API_KEY").strip())
    checks = {
        "package_importable": package_importable,
        "sdk_importable": sdk_importable,
        "entry_point": entry_point,
        "directory_manifest": directory_manifest,
        "plugin_enabled": enabled,
        "band_agent_id_present": agent_id_present,
        "band_api_key_present": api_key_present,
    }
    return {
        "success": (
            sdk_importable
            and (package_importable or directory_manifest)
            and (entry_point or directory_manifest)
            and enabled
            and agent_id_present
            and api_key_present
        ),
        "checks": checks,
        "missing": [name for name, ok in checks.items() if not ok],
    }


def main() -> int:
    result = verify_install()
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
