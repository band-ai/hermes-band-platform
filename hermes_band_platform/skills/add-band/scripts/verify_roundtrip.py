#!/usr/bin/env python3
"""Prove the agent can actually talk to its owner in the Hermes Hub.

``verify_gateway.py`` confirms the WebSocket connected and the hub was created,
but **connected is not working**: hub bootstrap runs in a ``try/except`` that
never blocks connect, and a send can still fail on auth, mentions, or room
state. This script closes that gap by exercising the *real* outbound path — the
same REST send a reply uses — against the hub room, and (optionally) waiting for
the owner to @mention back to prove the full duplex loop.

It reuses the live tool helpers, which fall back to a fresh REST client built
from ``BAND_API_KEY`` / ``BAND_BASE_URL`` when no in-process gateway is present —
exactly like a cron caller.

Run with the gateway interpreter:

    "$HERMES_PY" scripts/verify_roundtrip.py                 # outbound proof (default)
    "$HERMES_PY" scripts/verify_roundtrip.py --await-reply   # full duplex (waits for an @mention)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

# Make hermes_band_platform importable whether or not the package is pip-installed
# (this script may run from a cloned checkout). The gateway.* and band.* imports
# below still require running under the gateway interpreter ($HERMES_PY).
sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

DEFAULT_MESSAGE = (
    "Hermes setup check ✓ — your agent is connected and can post to this room. "
    "Reply by @mentioning me to confirm the full round-trip."
)


def _env_value(name: str) -> str:
    try:
        from hermes_cli.config import get_env_value

        return str(get_env_value(name) or "")
    except Exception:
        return os.getenv(name, "")


def _emit(payload: dict[str, Any]) -> int:
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload.get("success") else 1


async def _context_ids(rest: Any, room_id: str) -> set[str]:
    """Snapshot the message ids currently visible in the room (for reply diffing)."""
    from hermes_band_platform.adapter import DEFAULT_REQUEST_OPTIONS

    ids: set[str] = set()
    try:
        ctx = await rest.agent_api_context.get_agent_chat_context(
            chat_id=room_id, request_options=DEFAULT_REQUEST_OPTIONS
        )
    except Exception:
        return ids
    for item in getattr(ctx, "data", None) or []:
        mid = getattr(item, "id", None)
        if mid:
            ids.add(str(mid))
    return ids


async def _new_human_message(
    rest: Any, room_id: str, baseline_ids: set[str]
) -> Optional[str]:
    """Return the sender label of a new non-agent message, or None."""
    from hermes_band_platform.adapter import DEFAULT_REQUEST_OPTIONS

    try:
        ctx = await rest.agent_api_context.get_agent_chat_context(
            chat_id=room_id, request_options=DEFAULT_REQUEST_OPTIONS
        )
    except Exception:
        return None
    for item in getattr(ctx, "data", None) or []:
        mid = getattr(item, "id", None)
        if not mid or str(mid) in baseline_ids:
            continue
        sender_type = (
            getattr(item, "sender_type", None) or getattr(item, "type", None) or ""
        )
        if str(sender_type).lower() == "agent":
            continue
        return (
            getattr(item, "sender_name", None)
            or getattr(item, "name", None)
            or str(sender_type)
            or "?"
        )
    return None


async def _run(
    room_id: str, message: str, await_reply: bool, timeout: float
) -> dict[str, Any]:
    from band.client.rest import ChatMessageRequest

    from hermes_band_platform.adapter import DEFAULT_REQUEST_OPTIONS
    from hermes_band_platform.tools import _mentions_for, _rest

    rest = await _rest()

    # Snapshot BEFORE sending so a detected reply is genuinely new.
    baseline_ids: set[str] = await _context_ids(rest, room_id) if await_reply else set()

    mentions = await _mentions_for(rest, room_id, None)  # mention the owner(s)
    resp = await rest.agent_api_messages.create_agent_chat_message(
        room_id,
        message=ChatMessageRequest(content=message, mentions=mentions),
        request_options=DEFAULT_REQUEST_OPTIONS,
    )
    sent_id = getattr(getattr(resp, "data", None), "id", None)

    result: dict[str, Any] = {
        "success": bool(sent_id),
        "room_id": room_id,
        "sent_message_id": sent_id,
        "mentioned": len(mentions),
    }
    if not sent_id:
        result["error"] = "Hub send returned no message id"
        return result
    if not await_reply:
        return result

    result["awaited_reply"] = True
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        await asyncio.sleep(3)
        replier = await _new_human_message(rest, room_id, baseline_ids)
        if replier:
            result["reply_received"] = True
            result["reply_from"] = replier
            return result

    result["reply_received"] = False
    result["success"] = False
    result["error"] = f"No owner reply within {int(timeout)}s (the outbound send succeeded)"
    return result


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--await-reply",
        action="store_true",
        dest="await_reply",
        help="Also wait for the owner to @mention back (proves full duplex).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="Seconds to wait for a reply with --await-reply (default 120).",
    )
    parser.add_argument("--message", default=DEFAULT_MESSAGE)
    args = parser.parse_args(argv)

    room_id = _env_value("BAND_HUB_ROOM").strip() or _env_value("BAND_HOME_ROOM").strip()
    if not room_id:
        return _emit(
            {
                "success": False,
                "error": (
                    "No BAND_HUB_ROOM/BAND_HOME_ROOM set — restart the gateway so the "
                    "hub bootstraps first, then re-run."
                ),
            }
        )
    if not _env_value("BAND_API_KEY").strip():
        return _emit({"success": False, "error": "BAND_API_KEY not set"})

    try:
        result = asyncio.run(_run(room_id, args.message, args.await_reply, args.timeout))
    except Exception as exc:  # surface a bounded hint, never a secret
        return _emit(
            {"success": False, "room_id": room_id, "error": f"{type(exc).__name__}: {exc}"}
        )
    return _emit(result)


if __name__ == "__main__":
    sys.exit(main())
