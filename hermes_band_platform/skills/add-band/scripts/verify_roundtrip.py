#!/usr/bin/env python3
"""Prove the agent can actually talk to its owner in the Hermes Hub.

``verify_gateway.py`` confirms the WebSocket connected and the hub was created,
but **connected is not working**: hub bootstrap runs in a ``try/except`` that
never blocks connect, and a send can still fail on auth, mentions, or room
state. This script closes that gap by exercising the *real* outbound path — the
same REST send a reply uses — against the hub room, and (optionally) waiting for
the owner to @mention back to prove the full duplex loop.

Its only preconditions are the **Band SDK** and the agent's credentials — not an
importable plugin package and not a loadable host. It deliberately builds its own
short-lived REST client and its own mention list (~20 lines duplicated from
``tools.py``, pinned by a drift test) rather than importing the plugin: the
plugin's helpers pull in four ``gateway.*`` modules this script never uses, and
the package is not importable at all under a directory-plugin install, where the
tree is staged as ``$HERMES_HOME/plugins/band``.

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
from urllib.parse import urlsplit

# `-I` (used by install.sh and the e2e tests) does NOT put the script's own
# directory on sys.path, so the sibling helper needs an explicit entry. Only this
# scripts/ dir is added — never the plugin tree's parent, which would shadow the
# `band` SDK with the plugin package of the same name.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _plugin_env  # noqa: E402

DEFAULT_BAND_HOST = "app.band.ai"

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


def _rest_url(base_url: str) -> str:
    """``https://<host>`` from ``BAND_BASE_URL``.

    Mirrors ``adapter._derive_urls``'s REST half; pinned by
    ``test_verify_roundtrip_url_derivation_matches_the_adapter``.
    """
    host = DEFAULT_BAND_HOST
    raw = (base_url or "").strip()
    if raw:
        if "://" not in raw:
            raw = f"https://{raw}"
        parsed = urlsplit(raw)
        if parsed.hostname:
            host = parsed.hostname
            if parsed.port:
                host = f"{host}:{parsed.port}"
    return f"https://{host}"


async def _rest_client() -> Any:
    """A short-lived REST client from the agent's own credentials.

    ``tools._rest`` prefers the *live* adapter's link, but that branch is dead in
    a separate process (the runner ref is per-process), so this is the only path
    that ever executes for a standalone script — minus the host imports. It reads
    credentials through Hermes's env reader rather than ``os.getenv``, so it also
    works when they live only in the gateway's ``.env``.
    """
    from band.client.rest import AsyncRestClient

    api_key = _env_value("BAND_API_KEY").strip()
    if not api_key:
        raise RuntimeError("Band not configured (BAND_API_KEY)")
    return AsyncRestClient(api_key=api_key, base_url=_rest_url(_env_value("BAND_BASE_URL")))


async def _mentions_for(rest: Any, room_id: str) -> list[Any]:
    """Every non-agent participant in the room, as mention items.

    Band requires ≥1 mention per send, each carrying a non-null handle. This is
    ``tools._mentions_for(…, None)``'s fallback branch — the only one reachable
    without explicit ids — inlined: ``_list_participants`` +
    ``adapter._mention_plan``'s no-preferred path, with the agent's own id
    excluded so it never @mentions itself, and handle-less participants skipped
    (the API rejects ``handle: null``). Pinned by
    ``test_verify_roundtrip_mentions_match_the_adapter``.
    """
    from band.client.rest import DEFAULT_REQUEST_OPTIONS, ChatMessageRequestMentionsItem

    resp = await rest.agent_api_participants.list_agent_chat_participants(
        chat_id=room_id, request_options=DEFAULT_REQUEST_OPTIONS
    )
    agent_id = _env_value("BAND_AGENT_ID").strip() or None
    items: list[Any] = []
    for peer in getattr(resp, "data", None) or []:
        pid = getattr(peer, "id", None)
        if not pid or pid == agent_id or (getattr(peer, "type", None) or "") == "Agent":
            continue
        handle = str(getattr(peer, "handle", None) or "").strip()
        if not handle:
            continue
        items.append(
            ChatMessageRequestMentionsItem(
                id=pid,
                handle=handle,
                name=getattr(peer, "name", None),
            )
        )
    if not items:
        raise RuntimeError(
            "Band requires at least one @mention; no recipient with a Band handle was "
            "found in this room (add a participant to the room first)"
        )
    return items


async def _context_ids(rest: Any, room_id: str) -> set[str]:
    """Snapshot the message ids currently visible in the room (for reply diffing)."""
    from band.client.rest import DEFAULT_REQUEST_OPTIONS

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
    from band.client.rest import DEFAULT_REQUEST_OPTIONS

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
    from band.client.rest import DEFAULT_REQUEST_OPTIONS, ChatMessageRequest

    rest = await _rest_client()

    # Snapshot BEFORE sending so a detected reply is genuinely new.
    baseline_ids: set[str] = await _context_ids(rest, room_id) if await_reply else set()

    mentions = await _mentions_for(rest, room_id)  # mention the owner(s)
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

    # Resolve the layout FIRST — before the credential gates. A layout failure
    # must never hide behind "BAND_API_KEY not set", and getting past this point
    # is itself the proof that the tree, the Hermes home, band-libs, and the SDK
    # all resolved.
    layout = _plugin_env.resolve_layout(require_sdk=True)
    if layout.error:
        return _emit({"success": False, "error": layout.error, "layout": layout.as_dict()})

    room_id = _env_value("BAND_HUB_ROOM").strip() or _env_value("BAND_HOME_ROOM").strip()
    if not room_id:
        return _emit(
            {
                "success": False,
                "error": (
                    "No BAND_HUB_ROOM/BAND_HOME_ROOM set — restart the gateway so the "
                    "hub bootstraps first, then re-run."
                ),
                "layout": layout.as_dict(),
            }
        )
    if not _env_value("BAND_API_KEY").strip():
        return _emit(
            {"success": False, "error": "BAND_API_KEY not set", "layout": layout.as_dict()}
        )

    try:
        result = asyncio.run(_run(room_id, args.message, args.await_reply, args.timeout))
    except Exception as exc:  # surface a bounded hint, never a secret
        result = {
            "success": False,
            "room_id": room_id,
            "error": f"{type(exc).__name__}: {exc}",
        }
    result["layout"] = layout.as_dict()
    return _emit(result)


if __name__ == "__main__":
    sys.exit(main())
