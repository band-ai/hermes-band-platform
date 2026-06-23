#!/usr/bin/env python3
"""Ensure Band's hub room is set as the home (main) channel.

The home channel is where ``deliver=band`` cron jobs and agent-initiated
notifications land. The adapter wires the hub as home on connect and (current
builds) persists ``BAND_HOME_ROOM``; this script is the version-independent
**repair** for an agent already deployed on a build that only set the home
in-memory, so readers that load config fresh saw "no home set". Run it anytime
after the gateway has created the hub.

Behavior (idempotent):
  - ``BAND_HOME_ROOM`` already set  → leave it (respects an operator /sethome
    override too); nothing to do.
  - ``BAND_HOME_ROOM`` unset, ``BAND_HUB_ROOM`` set → persist
    ``BAND_HOME_ROOM = BAND_HUB_ROOM`` so the hub is the durable home.
  - neither set → the hub does not exist yet; (re)start the gateway so the
    adapter creates and persists it, then re-run.

Emits JSON.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any


def _env_value(name: str) -> str:
    try:
        from hermes_cli.config import get_env_value

        return str(get_env_value(name) or "")
    except Exception:
        return os.getenv(name, "")


def ensure_home_channel() -> dict[str, Any]:
    hub_room = _env_value("BAND_HUB_ROOM").strip()
    home_room = _env_value("BAND_HOME_ROOM").strip()

    if home_room:
        return {
            "success": True,
            "changed": False,
            "band_hub_room": hub_room,
            "band_home_room": home_room,
            "note": "Home channel already set; nothing to do.",
        }

    if not hub_room:
        return {
            "success": False,
            "changed": False,
            "band_hub_room": "",
            "band_home_room": "",
            "error": "no hub room yet — BAND_HUB_ROOM and BAND_HOME_ROOM are both unset",
            "action": (
                "(Re)start the gateway so the adapter creates the Hermes Hub and "
                "persists BAND_HUB_ROOM, then re-run this script."
            ),
        }

    try:
        from hermes_cli.config import save_env_value

        save_env_value("BAND_HOME_ROOM", hub_room)
    except Exception as exc:  # pragma: no cover - host write guard
        return {
            "success": False,
            "changed": False,
            "band_hub_room": hub_room,
            "band_home_room": "",
            "error": f"could not persist BAND_HOME_ROOM: {exc}",
            "action": (
                "Set BAND_HOME_ROOM to the BAND_HUB_ROOM value with Hermes's env "
                "writer, or via `hermes config set BAND_HOME_ROOM <hub-room-id>`."
            ),
        }

    return {
        "success": True,
        "changed": True,
        "band_hub_room": hub_room,
        "band_home_room": hub_room,
        "note": "Set BAND_HOME_ROOM = hub. Restart the gateway for it to take effect.",
    }


def main() -> int:
    result = ensure_home_channel()
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    sys.exit(main())
