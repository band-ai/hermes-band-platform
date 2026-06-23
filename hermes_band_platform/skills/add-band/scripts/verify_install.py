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


def _access_policy_allowlist() -> bool:
    """Whether Band's access policy authorizes Band traffic at the gateway.

    The gateway only trusts Band's own ACL when the effective policy for the
    chat type is ``"allowlist"`` (Band has no DMs, so traffic is group). True if
    the config records ``platforms.band.extra.group_policy = "allowlist"`` (the
    version-independent record written by ``ensure_access_policy.py``) or
    ``BAND_ALLOW_ALL`` is set. False (→ default-deny, "not an authorized user")
    when neither is present.
    """
    if _env_value("BAND_ALLOW_ALL").strip().lower() in {"true", "1", "yes"}:
        return True
    try:
        from hermes_cli.config import load_config

        config = load_config()
    except Exception:
        return False
    platforms = config.get("platforms", {}) if isinstance(config, dict) else {}
    band = platforms.get("band", {}) if isinstance(platforms, dict) else {}
    extra = band.get("extra", {}) if isinstance(band, dict) else {}
    return isinstance(extra, dict) and str(extra.get("group_policy", "")).strip().lower() == "allowlist"


def _conversations_skill_present() -> bool:
    """Whether the bundled ``band-conversations`` runtime skill ships with the
    install.

    This is the ``SKILL.md`` that ``adapter.register()`` registers as
    ``band:band-conversations`` — the multi-participant / delegation playbook the
    agent loads on demand from the Band platform hint. If it is missing, the
    agent still connects and chats but has no conversation playbook (an older
    build predating the skill). Checked the same two ways the install can ship:
    via the importable package (wheel) or relative to this script (directory
    manifest / editable).
    """
    try:
        spec = importlib.util.find_spec("hermes_band_platform")
        if spec is not None and spec.origin:
            pkg = Path(spec.origin).parent / "skills" / "band-conversations" / "SKILL.md"
            if pkg.is_file():
                return True
    except Exception:
        pass
    # Fallback: scripts/ -> add-band/ -> skills/ -> hermes_band_platform/
    pkg_dir = Path(__file__).resolve().parents[3]
    return (pkg_dir / "skills" / "band-conversations" / "SKILL.md").is_file()


def verify_install() -> dict[str, Any]:
    package_importable = importlib.util.find_spec("hermes_band_platform") is not None
    sdk_importable = importlib.util.find_spec("band") is not None
    entry_point = _has_band_entry_point()
    directory_manifest = _has_directory_manifest()
    enabled = _plugin_enabled()
    agent_id_present = bool(_env_value("BAND_AGENT_ID").strip())
    api_key_present = bool(_env_value("BAND_API_KEY").strip())
    access_policy = _access_policy_allowlist()
    conversations_skill = _conversations_skill_present()
    checks = {
        "package_importable": package_importable,
        "sdk_importable": sdk_importable,
        "entry_point": entry_point,
        "directory_manifest": directory_manifest,
        "plugin_enabled": enabled,
        "band_agent_id_present": agent_id_present,
        "band_api_key_present": api_key_present,
        "access_policy_allowlist": access_policy,
        "conversations_skill_present": conversations_skill,
    }
    missing = [name for name, ok in checks.items() if not ok]
    actions: list[str] = []
    if "package_importable" in missing or "entry_point" in missing:
        actions.append(
            "Install the package into the gateway Python: "
            "uv pip install --python \"$HERMES_PY\" "
            "\"hermes-band-platform @ git+https://github.com/band-ai/hermes-band-platform.git@${BAND_HERMES_REF:-main}\""
        )
    if "sdk_importable" in missing:
        actions.append(
            "band-sdk is missing. Directory plugin installs do not install Python "
            "dependencies; install it into the gateway Python with: "
            "uv pip install --python \"$HERMES_PY\" 'band-sdk>=1.0.0,<2.0.0' "
            "or \"$HERMES_PY\" -m pip install 'band-sdk>=1.0.0,<2.0.0'."
        )
    if "plugin_enabled" in missing:
        actions.append(
            "Enable the plugin with `hermes plugins enable band`; if the CLI does "
            "not list entry-point plugins, add `band` to plugins.enabled in the "
            "Hermes config."
        )
    if "band_agent_id_present" in missing or "band_api_key_present" in missing:
        actions.append(
            "Save agent-scoped credentials in Hermes env: BAND_AGENT_ID and "
            "BAND_API_KEY. Use scripts/register_agent.py with BAND_USER_API_KEY, "
            "or paste credentials from a pre-created Band external agent."
        )
    if "access_policy_allowlist" in missing:
        actions.append(
            "Configure Band's access policy so the gateway trusts Band's ACL "
            "(otherwise the agent rejects senders with 'not an authorized user'). "
            "Run: \"$HERMES_PY\" scripts/ensure_access_policy.py, then restart the gateway."
        )
    if "conversations_skill_present" in missing:
        actions.append(
            "The band-conversations runtime skill is missing (older build); the "
            "agent will connect but lack the multi-participant/delegation playbook. "
            "Upgrade the package in the gateway Python: uv pip install --python "
            "\"$HERMES_PY\" --upgrade \"hermes-band-platform @ "
            "git+https://github.com/band-ai/hermes-band-platform.git@${BAND_HERMES_REF:-main}\", "
            "then restart the gateway."
        )
    return {
        "success": (
            sdk_importable
            and (package_importable or directory_manifest)
            and (entry_point or directory_manifest)
            and enabled
            and agent_id_present
            and api_key_present
            and access_policy
            and conversations_skill
        ),
        "checks": checks,
        "missing": missing,
        "actions": actions,
    }


def main() -> int:
    result = verify_install()
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
