"""
Band contact-management tools for the Hermes agent.

Thin wrappers around band-sdk's own contacts helper
(``band.runtime.contact_tools.ContactTools``) -- a REST-backed contacts
subsystem (request/approve/reject, list contacts, list pending requests)
that band-sdk already ships. This module only adapts its plain-dict returns
into the plugin's ``tool_result``/``tool_error`` JSON envelope; it does not
reimplement any contact-request logic.

Conventions mirrored from ``tools.py``: lazy SDK import (the module loads
cleanly when ``band-sdk`` is absent), ``_tool_exc`` for error shaping.

``_contact_tools`` is the module's single SDK guard, and
``list_approved_contacts`` is the one place the ``list_contacts`` response
shape is unwrapped -- ``federation.py`` calls it rather than keeping a second
copy of either.
"""

from __future__ import annotations

from typing import Any, Dict, List

from tools.registry import tool_error, tool_result

from .tools import _authorize_band_action, _rest, _tool_exc, _ToolUnavailable

try:
    from band.runtime.contact_tools import ContactTools
except ImportError:  # SDK not present yet -- rebind in _contact_tools().
    ContactTools = None


def _contact_tools(rest: Any) -> Any:
    """Return a ``ContactTools`` bound to ``rest``, (re)binding the SDK class first.

    The single SDK guard for the contacts subsystem -- ``federation.py`` reaches
    it through ``list_approved_contacts`` rather than keeping a second copy.
    Rebinding on each call mirrors ``tools.py``'s ``_load_sdk()`` idiom, so a
    late ``pip install`` is picked up without a gateway restart.
    """
    global ContactTools
    if ContactTools is None:
        try:
            from band.runtime.contact_tools import ContactTools as _ContactTools
        except ImportError:
            raise _ToolUnavailable(
                "Band not available (band-sdk contacts module not installed)"
            ) from None
        ContactTools = _ContactTools
    return ContactTools(rest)


async def list_approved_contacts(rest: Any) -> List[Dict[str, Any]]:
    """Return the approved-contact dicts (``id``/``handle``/``name``/``type``).

    Shared by ``band_list_contacts`` and ``federation.py``'s friend
    resolution, so the response shape is unwrapped in exactly one place.
    """
    return (await _contact_tools(rest).list_contacts())["contacts"]


async def _handle_add_contact(args: dict, **kwargs) -> str:
    """band_add_contact: send a Band contact request."""
    try:
        _authorize_band_action()
        handle = str(args.get("handle") or "").strip()
        if not handle:
            return tool_error("handle is required")
        message = str(args.get("message") or "").strip() or None
        rest = await _rest()
        out = await _contact_tools(rest).add_contact(handle=handle, message=message)
        return tool_result({"success": True, **out})
    except Exception as exc:
        return _tool_exc(exc)


async def _handle_list_contacts(args: dict, **kwargs) -> str:
    """band_list_contacts: list approved contacts. Read-only."""
    try:
        rest = await _rest()
        return tool_result({"success": True, "contacts": await list_approved_contacts(rest)})
    except Exception as exc:
        return _tool_exc(exc)


async def _handle_list_contact_requests(args: dict, **kwargs) -> str:
    """band_list_contact_requests: list pending received/sent requests. Read-only."""
    try:
        rest = await _rest()
        out = await _contact_tools(rest).list_contact_requests()
        return tool_result(
            {"success": True, "received": out["received"], "sent": out["sent"]}
        )
    except Exception as exc:
        return _tool_exc(exc)


async def _handle_respond_contact_request(args: dict, **kwargs) -> str:
    """band_respond_contact_request: approve/reject/cancel a contact request."""
    try:
        _authorize_band_action()
        action = str(args.get("action") or "").strip().lower()
        if action not in ("approve", "reject", "cancel"):
            return tool_error("action must be one of: approve, reject, cancel")
        request_id = str(args.get("request_id") or "").strip()
        if not request_id:
            return tool_error("request_id is required")
        rest = await _rest()
        out = await _contact_tools(rest).respond_contact_request(
            action=action, request_id=request_id
        )
        return tool_result({"success": True, **out})
    except Exception as exc:
        return _tool_exc(exc)


# ---------------------------------------------------------------------------
# JSON schemas
# ---------------------------------------------------------------------------

BAND_ADD_CONTACT_SCHEMA = {
    "name": "band_add_contact",
    "description": (
        "Send a Band contact request to another Hermes agent (or user), by handle. "
        "The other side must approve before you become contacts."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "handle": {
                "type": "string",
                "description": "Handle to connect with, e.g. '@alice/hermes'.",
            },
            "message": {
                "type": "string",
                "description": "Optional note to include with the request.",
            },
        },
        "required": ["handle"],
    },
}

BAND_LIST_CONTACTS_SCHEMA = {
    "name": "band_list_contacts",
    "description": (
        "List your current approved Band contacts (people and other agents). Read-only."
    ),
    "parameters": {"type": "object", "properties": {}, "required": []},
}

BAND_LIST_CONTACT_REQUESTS_SCHEMA = {
    "name": "band_list_contact_requests",
    "description": "List pending Band contact requests you've received and sent. Read-only.",
    "parameters": {"type": "object", "properties": {}, "required": []},
}

BAND_RESPOND_CONTACT_REQUEST_SCHEMA = {
    "name": "band_respond_contact_request",
    "description": "Approve, reject, or cancel a Band contact request by its request_id.",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["approve", "reject", "cancel"],
                "description": "What to do with the request.",
            },
            "request_id": {"type": "string", "description": "The contact request's id."},
        },
        "required": ["action", "request_id"],
    },
}


# ---------------------------------------------------------------------------
# Registry tuple -- consumed by adapter.register():
#   for name, schema, handler, emoji in CONTACT_TOOLS: ctx.register_tool(...)
# ---------------------------------------------------------------------------

CONTACT_TOOLS = (
    ("band_add_contact", BAND_ADD_CONTACT_SCHEMA, _handle_add_contact, "🤝"),
    ("band_list_contacts", BAND_LIST_CONTACTS_SCHEMA, _handle_list_contacts, "📇"),
    (
        "band_list_contact_requests",
        BAND_LIST_CONTACT_REQUESTS_SCHEMA,
        _handle_list_contact_requests,
        "📥",
    ),
    (
        "band_respond_contact_request",
        BAND_RESPOND_CONTACT_REQUEST_SCHEMA,
        _handle_respond_contact_request,
        "✅",
    ),
)
