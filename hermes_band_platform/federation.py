"""
Federated LLM-wiki query tool for the Hermes agent.

``band_ask_wikis`` opens a fresh Band room, invites the requested (or every
approved agent-type) contact into it, posts the question mentioning them all,
and registers the query with the live ``BandAdapter``'s federation state
machine (``register_pending_federation`` -- see ``adapter.py``), which tracks
replies and finalizes with one synthesized-answer prompt once every friend
has replied or ``FEDERATION_TIMEOUT_SECONDS`` elapses, whichever is first.

This module owns only the "ask" side (room creation + registration); the
state machine itself lives in ``adapter.py`` since it needs the adapter's own
event loop (``_handle_message_created``) to observe replies.

Conventions mirrored from ``tools.py``: an independent lazy SDK import guard
for the request types this module constructs (see ``tools.py``'s own
docstring on why these guards are NOT shared across modules), and
``_tool_exc`` for error shaping.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from gateway.session_context import get_session_env
from tools.registry import tool_error, tool_result

from .adapter import DEFAULT_REQUEST_OPTIONS, FEDERATION_TIMEOUT_SECONDS
from .tools import (
    _authorize_band_action,
    _live_band_adapter,
    _rest,
    _tool_exc,
    _ToolError,
    _ToolUnavailable,
)

# ---------------------------------------------------------------------------
# Lazy SDK import guards. Independent copies -- see module docstring.
# ---------------------------------------------------------------------------
try:
    from band.client.rest import (  # noqa: F401
        ChatMessageRequest,
        ChatMessageRequestMentionsItem,
        ChatRoomRequest,
        ParticipantRequest,
    )
except ImportError:
    ChatMessageRequest = None
    ChatMessageRequestMentionsItem = None
    ChatRoomRequest = None
    ParticipantRequest = None

try:
    from band.runtime.contact_tools import ContactTools
except ImportError:
    ContactTools = None


def _load_request_types() -> bool:
    global ChatMessageRequest, ChatMessageRequestMentionsItem
    global ChatRoomRequest, ParticipantRequest
    if ChatRoomRequest is not None:
        return True
    try:
        from band.client.rest import (
            ChatMessageRequest as _ChatMessageRequest,
            ChatMessageRequestMentionsItem as _ChatMessageRequestMentionsItem,
            ChatRoomRequest as _ChatRoomRequest,
            ParticipantRequest as _ParticipantRequest,
        )
    except ImportError:
        return False
    ChatMessageRequest = _ChatMessageRequest
    ChatMessageRequestMentionsItem = _ChatMessageRequestMentionsItem
    ChatRoomRequest = _ChatRoomRequest
    ParticipantRequest = _ParticipantRequest
    return True


def _load_contact_tools() -> bool:
    global ContactTools
    if ContactTools is not None:
        return True
    try:
        from band.runtime.contact_tools import ContactTools as _ContactTools
    except ImportError:
        return False
    ContactTools = _ContactTools
    return True


def _match_agent_contact(
    contacts: List[Dict[str, Any]], query: str
) -> Optional[Dict[str, Any]]:
    """Resolve a free-text query to one contact dict by id/handle/name.

    Case-insensitive; prefers an exact id/handle match, then an exact name.
    """
    q = (query or "").strip().lower()
    if not q:
        return None
    for c in contacts:
        if (c.get("id") or "").lower() == q or (c.get("handle") or "").lower() == q:
            return c
    for c in contacts:
        if (c.get("name") or "").lower() == q:
            return c
    return None


def _resolve_requester_room(adapter: Any) -> str:
    """Where to deliver the final federated answer: current Band room, else the hub."""
    if get_session_env("HERMES_SESSION_PLATFORM") == "band":
        rid = (get_session_env("HERMES_SESSION_CHAT_ID") or "").strip()
        if rid:
            return rid
    home = getattr(adapter, "_hub_room_id", None)
    if home:
        return home
    raise _ToolError(
        "no room to deliver the federated answer to: ask from a Band room, or "
        "connect the gateway so the owner hub exists"
    )


async def _handle_ask_wikis(args: dict, **kwargs) -> str:
    """band_ask_wikis: fan a question out to friend agents and track replies.

    Tool-call order is load-bearing: resolve targets -> create room -> add
    participants -> post the query -> register the pending state. Any
    failure before registration leaves no orphaned pending-federation entry.
    """
    try:
        _authorize_band_action()
        if not _load_request_types():
            raise _ToolUnavailable("Band not available (band-sdk not installed)")
        if not _load_contact_tools():
            raise _ToolUnavailable(
                "Band not available (band-sdk contacts module not installed)"
            )

        query = str(args.get("query") or "").strip()
        if not query:
            return tool_error("query is required")
        local_findings = str(args.get("local_findings") or "").strip() or None
        requested = args.get("friends")
        if requested is not None and not isinstance(requested, list):
            requested = [requested]

        adapter = _live_band_adapter()
        if adapter is None:
            raise _ToolUnavailable(
                "band_ask_wikis requires a live Band gateway connection in this process"
            )

        rest = await _rest()
        contacts = (await ContactTools(rest).list_contacts())["contacts"]
        agent_contacts = [c for c in contacts if (c.get("type") or "") == "Agent"]

        warning = None
        if requested:
            targets = []
            unresolved = []
            for name in requested:
                match = _match_agent_contact(agent_contacts, str(name))
                if match:
                    targets.append(match)
                else:
                    unresolved.append(str(name))
            if not targets:
                return tool_error(
                    f"none of the requested friends are agent contacts: {requested}"
                )
            if unresolved:
                warning = f"could not resolve: {', '.join(unresolved)}"
        else:
            targets = agent_contacts
            if not targets:
                return tool_error(
                    "no agent contacts yet -- use band_add_contact to connect with a "
                    "friend's Hermes agent first"
                )

        created = await rest.agent_api_chats.create_agent_chat(
            chat=ChatRoomRequest(), request_options=DEFAULT_REQUEST_OPTIONS
        )
        room_id = getattr(getattr(created, "data", None), "id", None)
        if not room_id:
            raise _ToolError("Band did not return a room id for the federation room")

        friend_names: Dict[str, str] = {}
        mentions = []
        for t in targets:
            await rest.agent_api_participants.add_agent_chat_participant(
                room_id,
                participant=ParticipantRequest(participant_id=t["id"], role="member"),
                request_options=DEFAULT_REQUEST_OPTIONS,
            )
            friend_names[t["id"]] = t.get("name") or t.get("handle") or t["id"]
            mentions.append(
                ChatMessageRequestMentionsItem(
                    id=t["id"], handle=t.get("handle"), name=t.get("name")
                )
            )

        await rest.agent_api_messages.create_agent_chat_message(
            room_id,
            message=ChatMessageRequest(content=query, mentions=mentions),
            request_options=DEFAULT_REQUEST_OPTIONS,
        )

        requester_room_id = _resolve_requester_room(adapter)
        adapter.register_pending_federation(
            room_id=room_id,
            query=query,
            local_findings=local_findings,
            requester_room_id=requester_room_id,
            friend_names=friend_names,
        )

        result: Dict[str, Any] = {
            "success": True,
            "room_id": room_id,
            "asked": list(friend_names.values()),
            "timeout_seconds": FEDERATION_TIMEOUT_SECONDS,
        }
        if warning:
            result["warning"] = warning
        return tool_result(result)
    except Exception as exc:
        return _tool_exc(exc)


# ---------------------------------------------------------------------------
# JSON schema
# ---------------------------------------------------------------------------

BAND_ASK_WIKIS_SCHEMA = {
    "name": "band_ask_wikis",
    "description": (
        "Federate a question to your connected Hermes friends' agents so each can "
        "search its own LLM wiki and reply. Opens a fresh room, posts the question "
        "mentioning every target friend, and returns immediately -- you'll get a "
        "consolidated answer as a follow-up message once every friend has replied or "
        "5 minutes pass, whichever is first. Search your OWN wiki first (via the "
        "research-llm-wiki skill) and pass what you found as local_findings so it's "
        "included in the final combined answer."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "The question to federate."},
            "friends": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Optional: specific friends to ask (handle, name, or id). Omit to "
                    "ask every connected agent-type contact."
                ),
            },
            "local_findings": {
                "type": "string",
                "description": (
                    "Optional: what your own wiki search already found, to fold into "
                    "the final answer."
                ),
            },
        },
        "required": ["query"],
    },
}


# ---------------------------------------------------------------------------
# Registry tuple -- consumed by adapter.register():
#   for name, schema, handler, emoji in FEDERATION_TOOLS: ctx.register_tool(...)
# ---------------------------------------------------------------------------

FEDERATION_TOOLS = (
    ("band_ask_wikis", BAND_ASK_WIKIS_SCHEMA, _handle_ask_wikis, "🌐"),
)
