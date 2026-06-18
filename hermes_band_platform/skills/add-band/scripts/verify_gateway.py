#!/usr/bin/env python3
"""Verify Band gateway connection and Hermes Hub readiness."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

SUCCESS_PATTERNS = (
    re.compile(r"\[band\]\s+Connected as agent", re.IGNORECASE),
    re.compile(r"\[band\]\s+Hub ready:\s+room", re.IGNORECASE),
    re.compile(r"✓\s*band connected", re.IGNORECASE),
)
FAILURE_PATTERNS = (
    re.compile(r"\[band\]\s+Owner unresolved", re.IGNORECASE),
    re.compile(r"\[band\]\s+Hub bootstrap failed", re.IGNORECASE),
    re.compile(r"requirements not met", re.IGNORECASE),
)


def _hermes_home() -> Path:
    try:
        from hermes_constants import get_hermes_home

        return get_hermes_home()
    except Exception:
        return Path(os.getenv("HERMES_HOME", str(Path.home() / ".hermes"))).expanduser()


def _env_value(name: str) -> str:
    try:
        from hermes_cli.config import get_env_value

        return str(get_env_value(name) or "")
    except Exception:
        return os.getenv(name, "")


def _read_recent_log(path: Path, max_bytes: int = 256_000) -> str:
    if not path.exists():
        return ""
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - max_bytes), os.SEEK_SET)
            return handle.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def verify_gateway(log_path: Path | None = None) -> dict[str, Any]:
    home = _hermes_home()
    gateway_log = log_path or (home / "logs" / "gateway.log")
    log_text = _read_recent_log(gateway_log)
    success_hits = [
        pattern.pattern for pattern in SUCCESS_PATTERNS if pattern.search(log_text)
    ]
    failure_hits = [
        pattern.pattern for pattern in FAILURE_PATTERNS if pattern.search(log_text)
    ]
    hub_room = _env_value("BAND_HUB_ROOM").strip()
    home_room = _env_value("BAND_HOME_ROOM").strip()
    # A working setup has a resolved main channel — normally the auto-persisted
    # hub (BAND_HUB_ROOM), but an explicit BAND_HOME_ROOM override is equally
    # valid. Gating on the hub alone would report failure for a fully functional
    # gateway whose owner pinned a home room instead.
    main_channel = hub_room or home_room
    return {
        "success": bool(main_channel and success_hits and not failure_hits),
        "band_hub_room_present": bool(hub_room),
        "band_home_room_present": bool(home_room),
        "gateway_log": str(gateway_log),
        "success_signals": success_hits,
        "failure_signals": failure_hits,
    }


def main() -> int:
    result = verify_gateway()
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
