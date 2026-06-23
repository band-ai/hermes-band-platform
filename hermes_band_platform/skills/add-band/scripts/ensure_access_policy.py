#!/usr/bin/env python3
"""Ensure Band's access policy is configured so the gateway trusts Band's ACL.

Band owns its own access control: a message only reaches the agent if Band
admitted the sender. The gateway's authorization gate
(``gateway.authz_mixin._is_user_authorized``) only honors an own-policy adapter's
intake when its effective policy for the chat type is literally ``"allowlist"``
(the host's fail-open hardening). Band has no DMs, so every source is
``chat_type="group"`` and the host reads ``group_policy``.

The current plugin sets this on the live adapter in code, but writing it to
config too makes a fresh install authorize regardless of plugin version and lets
this be a **repair** step: run it anytime an already-deployed Band agent rejects
its owner with "not an authorized user", then restart the gateway — no plugin
reinstall needed.

Writes ``platforms.band.extra.{dm_policy,group_policy} = "allowlist"`` into the
Hermes config. Idempotent: re-running is a no-op once both are set. Emits JSON.
"""

from __future__ import annotations

import json
import sys
from typing import Any

_POLICY_KEYS = ("dm_policy", "group_policy")
_POLICY_VALUE = "allowlist"


def ensure_access_policy() -> dict[str, Any]:
    try:
        from hermes_cli.config import load_config, save_config
    except Exception as exc:  # pragma: no cover - host import guard
        return {
            "success": False,
            "changed": False,
            "error": f"could not import hermes_cli.config: {exc}",
            "action": (
                "Run this with the gateway interpreter ($HERMES_PY) so Hermes's "
                "config module is importable."
            ),
        }

    config = load_config()
    platforms = config.setdefault("platforms", {})
    if not isinstance(platforms, dict):
        platforms = {}
        config["platforms"] = platforms
    band = platforms.setdefault("band", {})
    if not isinstance(band, dict):
        band = {}
        platforms["band"] = band
    extra = band.setdefault("extra", {})
    if not isinstance(extra, dict):
        extra = {}
        band["extra"] = extra

    before = {k: extra.get(k) for k in _POLICY_KEYS}
    changed = False
    for key in _POLICY_KEYS:
        if extra.get(key) != _POLICY_VALUE:
            extra[key] = _POLICY_VALUE
            changed = True

    if changed:
        try:
            save_config(config)
        except Exception as exc:  # pragma: no cover - host write guard
            return {
                "success": False,
                "changed": False,
                "error": f"save_config failed: {exc}",
                "action": (
                    "If the config is managed by your administrator, set "
                    "platforms.band.extra.group_policy and dm_policy to "
                    "'allowlist' in the managed layer instead."
                ),
            }

    after = {k: _POLICY_VALUE for k in _POLICY_KEYS}
    return {
        "success": True,
        "changed": changed,
        "before": before,
        "after": after,
        "note": (
            "Restart the gateway for the change to take effect."
            if changed
            else "Already configured; nothing to do."
        ),
    }


def main() -> int:
    result = ensure_access_policy()
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    sys.exit(main())
