# Federated LLM Wiki Query Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a Hermes agent connect to other Hermes agents over Band (native contact-request approve/reject) and federate an LLM-wiki question across approved friend agents, with deterministic reply tracking, a 5-minute timeout, and one synthesized answer delivered back to the user.

**Architecture:** Two independent additions wired into the existing `BandAdapter`/`tools.py` pattern: (1) turn on `band-sdk`'s already-existing but unused contact-request subsystem and route its events into the owner's existing Hub room; (2) a small deterministic state machine (`_PendingFederation`, keyed by room id) inside `BandAdapter` that tracks an in-flight federated query across a fresh per-query room, intercepted early in `_handle_message_created`, and a `band_ask_wikis` tool that creates the room and registers the pending state.

**Tech Stack:** Python 3.11+, `band-sdk` 1.0.0 (`band.runtime.contact_tools.ContactTools`, `agent_api_contacts`, `agent_api_chats`, `agent_api_participants`, `agent_api_messages`), Hermes gateway (`hermes-agent` 0.17, `gateway.platforms.base`), `pytest` + `pytest-asyncio`.

## Global Constraints

- Every commit must be DCO-signed: use `git commit -s`.
- Federation timeout is a fixed constant (`FEDERATION_TIMEOUT_SECONDS = 300`), not a new env var — per Mr. Ofer's decision to favor simplicity (KISS) over an unrequested config knob.
- No new required/optional env vars — this feature needs no new credentials (per the approved spec, `docs/superpowers/specs/2026-07-04-federated-wiki-query-design.md`).
- Follow the codebase's established convention: each module that constructs `band-sdk` request types keeps its own independent lazy-import guard + rebind function (see `tools.py`'s own copy of the `ChatMessageRequest`/`ChatRoomRequest`/etc. guard, separate from `adapter.py`'s) — do not cross-import these mutable/rebindable names between modules.
- `adapter.py` must keep importing cleanly with no intra-package imports at module level (existing invariant — `tools.py`/`federation.py`/`contacts.py` import *from* `adapter.py`, never the reverse; `register()` does its `from . import tools` / `from . import contacts` / `from . import federation` as **local** imports inside the function body, exactly like the existing `from . import tools as _band_tools` at `adapter.py:2584`).
- Run `pytest` from the repo root (`/Users/ofer/dev/band/hermes-band-platform`); `tests/conftest.py` installs a fake `band` SDK into `sys.modules` before any test module imports the plugin, so tests do not require the real `band-sdk` to be installed.

---

### Task 1: Contact-management tools (`contacts.py`)

**Files:**
- Create: `hermes_band_platform/contacts.py`
- Create: `hermes_band_platform/skills/band-contacts/SKILL.md`
- Modify: `hermes_band_platform/adapter.py:2584-2628` (the `register()` function — register the 4 new tools + new skill, alongside the existing `BAND_TOOLS` loop)
- Test: `tests/test_contacts.py`
- Test: `tests/test_adapter.py` (append `TestContactToolRegistration` covering the new registrations)

**Interfaces:**
- Consumes: `hermes_band_platform.tools._rest` (async, returns an authenticated REST client or raises `_ToolUnavailable`), `hermes_band_platform.tools._tool_exc(exc) -> str`, `hermes_band_platform.tools._ToolUnavailable`, `hermes_band_platform.tools._check_band_tools_available() -> bool`, `tools.registry.tool_error(msg) -> str`, `tools.registry.tool_result(data) -> str`.
- Produces: `CONTACT_TOOLS` — a tuple of `(name, schema, handler, emoji)` in the same shape as `hermes_band_platform.tools.BAND_TOOLS`, consumed by `register()` in `adapter.py`. Handler names: `_handle_add_contact`, `_handle_list_contacts`, `_handle_list_contact_requests`, `_handle_respond_contact_request`. Also produces `_load_contact_tools() -> bool` and the module-level `ContactTools` name (rebound by that function), for Task 4 (`federation.py`) to reuse when resolving agent-type contacts.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_contacts.py`:

```python
"""Tests for the Band contact-management tools (``hermes_band_platform/contacts.py``).

These wrap band-sdk's own ``ContactTools`` helper (``band.runtime.contact_tools``),
so tests patch ``contacts.ContactTools`` directly (mirroring how
``tests/test_tools.py`` patches ``tools._rest``) rather than needing a
``sys.modules`` stub for ``band.runtime.contact_tools`` -- that submodule isn't
registered by ``tests/conftest.py``, so ``contacts.py``'s own lazy import of it
fails closed to ``None`` in this test environment, exactly as it would with an
older band-sdk missing this module.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hermes_band_platform import contacts as band_contacts


def _make_contact_tools(**overrides) -> MagicMock:
    """Fake ContactTools instance with sensible AsyncMock defaults."""
    fake = MagicMock()
    fake.list_contacts = AsyncMock(return_value={"contacts": [], "metadata": {}})
    fake.add_contact = AsyncMock(return_value={"id": "req-1", "status": "pending"})
    fake.remove_contact = AsyncMock(return_value={"status": "removed"})
    fake.list_contact_requests = AsyncMock(
        return_value={"received": [], "sent": [], "metadata": {}}
    )
    fake.respond_contact_request = AsyncMock(
        return_value={"id": "req-1", "status": "approved"}
    )
    for key, value in overrides.items():
        setattr(fake, key, value)
    return fake


def _patch_contact_tools(fake):
    return patch.object(band_contacts, "ContactTools", MagicMock(return_value=fake))


def _patch_rest():
    return patch.object(band_contacts, "_rest", AsyncMock(return_value=MagicMock()))


def _parse(result: str) -> dict:
    assert isinstance(result, str)
    return json.loads(result)


class TestAddContact:

    @pytest.mark.asyncio
    async def test_sends_request(self):
        fake = _make_contact_tools(
            add_contact=AsyncMock(return_value={"id": "req-9", "status": "pending"})
        )
        with _patch_rest(), _patch_contact_tools(fake):
            out = _parse(
                await band_contacts._handle_add_contact(
                    {"handle": "@alice/hermes", "message": "hi"}
                )
            )
        assert out["success"] is True
        assert out["id"] == "req-9"
        assert out["status"] == "pending"
        fake.add_contact.assert_awaited_once_with(handle="@alice/hermes", message="hi")

    @pytest.mark.asyncio
    async def test_requires_handle(self):
        out = _parse(await band_contacts._handle_add_contact({}))
        assert "error" in out


class TestListContacts:

    @pytest.mark.asyncio
    async def test_lists(self):
        fake = _make_contact_tools(
            list_contacts=AsyncMock(
                return_value={
                    "contacts": [
                        {"id": "c1", "handle": "bob/hermes", "name": "Bob", "type": "Agent"}
                    ],
                    "metadata": {},
                }
            )
        )
        with _patch_rest(), _patch_contact_tools(fake):
            out = _parse(await band_contacts._handle_list_contacts({}))
        assert out["success"] is True
        assert out["contacts"] == [
            {"id": "c1", "handle": "bob/hermes", "name": "Bob", "type": "Agent"}
        ]


class TestListContactRequests:

    @pytest.mark.asyncio
    async def test_lists_received_and_sent(self):
        fake = _make_contact_tools(
            list_contact_requests=AsyncMock(
                return_value={
                    "received": [{"id": "r1", "from_handle": "carol"}],
                    "sent": [],
                    "metadata": {},
                }
            )
        )
        with _patch_rest(), _patch_contact_tools(fake):
            out = _parse(await band_contacts._handle_list_contact_requests({}))
        assert out["success"] is True
        assert out["received"] == [{"id": "r1", "from_handle": "carol"}]
        assert out["sent"] == []


class TestRespondContactRequest:

    @pytest.mark.asyncio
    async def test_approves(self):
        fake = _make_contact_tools(
            respond_contact_request=AsyncMock(
                return_value={"id": "req-1", "status": "approved"}
            )
        )
        with _patch_rest(), _patch_contact_tools(fake):
            out = _parse(
                await band_contacts._handle_respond_contact_request(
                    {"action": "approve", "request_id": "req-1"}
                )
            )
        assert out["success"] is True
        assert out["status"] == "approved"
        fake.respond_contact_request.assert_awaited_once_with(
            action="approve", request_id="req-1"
        )

    @pytest.mark.asyncio
    async def test_rejects_invalid_action(self):
        out = _parse(
            await band_contacts._handle_respond_contact_request(
                {"action": "smash", "request_id": "req-1"}
            )
        )
        assert "error" in out

    @pytest.mark.asyncio
    async def test_requires_request_id(self):
        out = _parse(
            await band_contacts._handle_respond_contact_request({"action": "approve"})
        )
        assert "error" in out


class TestContactToolsUnavailable:

    @pytest.mark.asyncio
    async def test_add_contact_reports_unavailable_when_sdk_missing(self, monkeypatch):
        monkeypatch.setattr(band_contacts, "ContactTools", None)
        monkeypatch.setattr(
            band_contacts, "_load_contact_tools", lambda: False
        )
        out = _parse(await band_contacts._handle_add_contact({"handle": "@x/y"}))
        assert "error" in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/ofer/dev/band/hermes-band-platform && pytest tests/test_contacts.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'hermes_band_platform.contacts'`

- [ ] **Step 3: Create `hermes_band_platform/contacts.py`**

```python
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
"""

from __future__ import annotations

from typing import Any

from tools.registry import tool_error, tool_result

from .tools import _check_band_tools_available, _rest, _tool_exc, _ToolUnavailable

try:
    from band.runtime.contact_tools import ContactTools
except ImportError:  # SDK not present yet -- rebind in _load_contact_tools().
    ContactTools = None


def _load_contact_tools() -> bool:
    """(Re)bind ``ContactTools`` so a late ``pip install`` is picked up live.

    Mirrors ``tools.py``'s own ``_load_sdk()`` rebind idiom.
    """
    global ContactTools
    if ContactTools is not None:
        return True
    try:
        from band.runtime.contact_tools import ContactTools as _ContactTools
    except ImportError:
        return False
    ContactTools = _ContactTools
    return True


async def _handle_add_contact(args: dict, **kwargs) -> str:
    """band_add_contact: send a Band contact request."""
    try:
        if not _load_contact_tools():
            raise _ToolUnavailable("Band not available (band-sdk not installed)")
        handle = str(args.get("handle") or "").strip()
        if not handle:
            return tool_error("handle is required")
        message = str(args.get("message") or "").strip() or None
        rest = await _rest()
        out = await ContactTools(rest).add_contact(handle=handle, message=message)
        return tool_result({"success": True, **out})
    except Exception as exc:
        return _tool_exc(exc)


async def _handle_list_contacts(args: dict, **kwargs) -> str:
    """band_list_contacts: list approved contacts. Read-only."""
    try:
        if not _load_contact_tools():
            raise _ToolUnavailable("Band not available (band-sdk not installed)")
        rest = await _rest()
        out = await ContactTools(rest).list_contacts()
        return tool_result({"success": True, "contacts": out["contacts"]})
    except Exception as exc:
        return _tool_exc(exc)


async def _handle_list_contact_requests(args: dict, **kwargs) -> str:
    """band_list_contact_requests: list pending received/sent requests. Read-only."""
    try:
        if not _load_contact_tools():
            raise _ToolUnavailable("Band not available (band-sdk not installed)")
        rest = await _rest()
        out = await ContactTools(rest).list_contact_requests()
        return tool_result(
            {"success": True, "received": out["received"], "sent": out["sent"]}
        )
    except Exception as exc:
        return _tool_exc(exc)


async def _handle_respond_contact_request(args: dict, **kwargs) -> str:
    """band_respond_contact_request: approve/reject/cancel a contact request."""
    try:
        if not _load_contact_tools():
            raise _ToolUnavailable("Band not available (band-sdk not installed)")
        action = str(args.get("action") or "").strip().lower()
        if action not in ("approve", "reject", "cancel"):
            return tool_error("action must be one of: approve, reject, cancel")
        request_id = str(args.get("request_id") or "").strip()
        if not request_id:
            return tool_error("request_id is required")
        rest = await _rest()
        out = await ContactTools(rest).respond_contact_request(
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_contacts.py -v`
Expected: PASS (11 tests)

- [ ] **Step 5: Create the `band:contacts` skill**

Create `hermes_band_platform/skills/band-contacts/SKILL.md`:

```markdown
---
name: band-contacts
description: "How to handle incoming Band contact requests and manage friend connections between Hermes agents."
version: 1.0.0
metadata:
  hermes:
    tags: [band, contacts, federation, multi-agent]
    requires_tools: [band_respond_contact_request]
---

# Handling Band contact requests

Your Hermes Agent Hub receives a system line whenever a Band contact event
happens -- someone (often another Hermes agent) wants to connect with you, or
a request you sent changes status. These lines are injected directly into
your Hub conversation, the same room you already talk to your owner in.

## What you'll see

```
[Contact Request] Alice (@alice/hermes) wants to connect.
Message: "let's federate wiki searches"
Request ID: abc-123
```

```
[Contact Request Update] Request abc-123 status changed to: approved
```

```
[Contact Added] Alice (@alice/hermes) is now a contact.
Type: Agent, ID: c-456
```

```
[Contact Removed] Contact c-456 was removed.
```

## What to do

- **`[Contact Request]`**: tell your owner who is asking and why (include
  their message, if any), then wait for an instruction before approving or
  rejecting -- unless your owner has already told you, in this conversation,
  to auto-approve requests. Use `band_respond_contact_request(action=...,
  request_id=...)` to act.
- **`[Contact Request Update]` / `[Contact Added]` / `[Contact Removed]`**:
  these are informational. Mention them to your owner in passing if relevant
  to what you're discussing; don't proactively interrupt with a notice unless
  your owner is actively talking to you about contacts.
- Never approve or reject a request the owner hasn't weighed in on, unless
  they've given you a standing policy to follow.

## Tools at a glance

| Tool | Use |
|------|-----|
| `band_add_contact` | Send a connection request to another agent's Band handle |
| `band_list_contacts` | See your current approved contacts |
| `band_list_contact_requests` | See pending requests you've sent/received |
| `band_respond_contact_request` | Approve, reject, or cancel a request |
```

- [ ] **Step 6: Register the contact tools and skill in `register()`**

In `hermes_band_platform/adapter.py`, modify the `register()` function. Change:

```python
    from . import tools as _band_tools

    for name, schema, handler, emoji in _band_tools.BAND_TOOLS:
        ctx.register_tool(
            name=name,
            toolset="band",
            schema=schema,
            handler=handler,
            check_fn=_band_tools._check_band_tools_available,
            is_async=True,
            emoji=emoji,
        )
```

to:

```python
    from . import contacts as _band_contacts
    from . import tools as _band_tools

    for name, schema, handler, emoji in _band_tools.BAND_TOOLS:
        ctx.register_tool(
            name=name,
            toolset="band",
            schema=schema,
            handler=handler,
            check_fn=_band_tools._check_band_tools_available,
            is_async=True,
            emoji=emoji,
        )

    for name, schema, handler, emoji in _band_contacts.CONTACT_TOOLS:
        ctx.register_tool(
            name=name,
            toolset="band",
            schema=schema,
            handler=handler,
            check_fn=_band_tools._check_band_tools_available,
            is_async=True,
            emoji=emoji,
        )
```

And add the skill registration inside the existing `try:` block that registers `add-band` and `band-conversations` (right after the `band-conversations` block, still inside the same `try`):

```python
        _contacts_md = (
            _SkillPath(__file__).parent / "skills" / "band-contacts" / "SKILL.md"
        )
        if _contacts_md.exists():
            ctx.register_skill(
                "band-contacts",
                _contacts_md,
                description=(
                    "Handle incoming Band contact requests and manage friend "
                    "connections between Hermes agents."
                ),
            )
```

- [ ] **Step 7: Add a registration test**

Append to `tests/test_adapter.py` (after the existing `TestBandPluginRegistration` class, around line 273):

```python
# ---------------------------------------------------------------------------
# Contact tool + skill registration (federated-wiki-query design, Part 1)
# ---------------------------------------------------------------------------

class TestContactToolRegistration:

    def test_register_registers_contact_tools(self):
        ctx = MagicMock()
        register(ctx)
        names = {c.kwargs["name"] for c in ctx.register_tool.call_args_list}
        for expected in (
            "band_add_contact",
            "band_list_contacts",
            "band_list_contact_requests",
            "band_respond_contact_request",
        ):
            assert expected in names

    def test_register_registers_band_contacts_skill(self):
        ctx = MagicMock()
        register(ctx)
        skill_names = {c.args[0] for c in ctx.register_skill.call_args_list}
        assert "band-contacts" in skill_names
```

- [ ] **Step 8: Run the full test file to verify no regressions**

Run: `pytest tests/test_contacts.py tests/test_adapter.py -v`
Expected: PASS (all tests, including the two new ones and all pre-existing ones)

- [ ] **Step 9: Commit**

```bash
cd /Users/ofer/dev/band/hermes-band-platform
git add hermes_band_platform/contacts.py hermes_band_platform/skills/band-contacts/SKILL.md hermes_band_platform/adapter.py tests/test_contacts.py tests/test_adapter.py
git commit -s -m "feat(contacts): add Band contact-management tools and skill

Wraps band-sdk's existing ContactTools (agent_api_contacts) as four
Hermes tools -- band_add_contact, band_list_contacts,
band_list_contact_requests, band_respond_contact_request -- and adds
the band:contacts skill teaching the owner-Hub LLM how to react to
incoming contact-request notices."
```

---

### Task 2: Wire contact_* events into the owner Hub

**Files:**
- Modify: `hermes_band_platform/adapter.py` (imports; `_handle_event`; new `_format_contact_event` pure function; new `_handle_contact_event` method; `connect()`; new `_subscribe_agent_contacts_safe` method)
- Test: `tests/test_adapter.py` (new `TestFormatContactEvent`, `TestContactEvents` classes; fix the now-stale `test_unhandled_event_type_is_ignored`; add a `connect()` subscription test)

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `_format_contact_event(event) -> Optional[str]` (module-level pure function in `adapter.py`) and `BandAdapter._handle_contact_event(event) -> None`, `BandAdapter._subscribe_agent_contacts_safe() -> None`. Not consumed by later tasks, but establishes the "Hub injection" idiom Task 3 reuses for federation digests.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_adapter.py` (after `TestHandleEvent`, i.e. after line 866, before the `TestParticipantChangeEvents` section header):

```python
# ---------------------------------------------------------------------------
# Contact events -> owner Hub injection (federated-wiki-query design, Part 1)
# ---------------------------------------------------------------------------

class TestFormatContactEvent:

    def test_request_received_without_message(self):
        payload = SimpleNamespace(
            id="r1", from_handle="dan", from_name="Dan", message=None,
            status="pending", inserted_at="2026-01-01T00:00:00Z",
        )
        text = _band_mod._format_contact_event(
            SimpleNamespace(type="contact_request_received", payload=payload)
        )
        assert "[Contact Request]" in text
        assert "Dan" in text
        assert "@dan" in text
        assert "r1" in text
        assert "Message:" not in text

    def test_request_received_with_message(self):
        payload = SimpleNamespace(
            id="r2", from_handle="@eve/hermes", from_name="Eve",
            message="let's federate", status="pending", inserted_at="x",
        )
        text = _band_mod._format_contact_event(
            SimpleNamespace(type="contact_request_received", payload=payload)
        )
        assert "@eve/hermes" in text
        assert "let's federate" in text

    def test_request_updated(self):
        payload = SimpleNamespace(id="r3", status="approved")
        text = _band_mod._format_contact_event(
            SimpleNamespace(type="contact_request_updated", payload=payload)
        )
        assert "[Contact Request Update]" in text
        assert "r3" in text
        assert "approved" in text

    def test_contact_added(self):
        payload = SimpleNamespace(id="c1", handle="bob/hermes", name="Bob", type="Agent")
        text = _band_mod._format_contact_event(
            SimpleNamespace(type="contact_added", payload=payload)
        )
        assert "[Contact Added]" in text
        assert "Bob" in text
        assert "Agent" in text
        assert "c1" in text

    def test_contact_removed(self):
        payload = SimpleNamespace(id="c2")
        text = _band_mod._format_contact_event(
            SimpleNamespace(type="contact_removed", payload=payload)
        )
        assert "[Contact Removed]" in text
        assert "c2" in text

    def test_unknown_type_returns_none(self):
        assert (
            _band_mod._format_contact_event(
                SimpleNamespace(type="something_else", payload=SimpleNamespace())
            )
            is None
        )

    def test_none_payload_returns_none(self):
        assert (
            _band_mod._format_contact_event(
                SimpleNamespace(type="contact_added", payload=None)
            )
            is None
        )


class TestContactEvents:
    """contact_* events are injected into the owner Hub session."""

    @pytest.fixture
    def adapter(self, monkeypatch):
        a = _make_adapter(monkeypatch, agent_id="agent-self-id")
        a._agent_id = "agent-self-id"
        a._hub_room_id = "hub-room-1"
        a.handle_message = AsyncMock()
        return a

    @pytest.mark.asyncio
    async def test_contact_request_received_is_injected_into_hub(self, adapter):
        payload = SimpleNamespace(
            id="req-1", from_handle="alice/hermes", from_name="Alice",
            message="let's connect", status="pending", inserted_at="2026-01-01T00:00:00Z",
        )
        event = SimpleNamespace(type="contact_request_received", room_id=None, payload=payload)
        await adapter._handle_event(event)
        adapter.handle_message.assert_called_once()
        evt = adapter.handle_message.call_args[0][0]
        assert evt.internal is True
        assert evt.source.chat_id == "hub-room-1"
        assert "[Contact Request]" in evt.text
        assert "Alice" in evt.text

    @pytest.mark.asyncio
    async def test_contact_added_is_injected(self, adapter):
        payload = SimpleNamespace(id="c-1", handle="bob/hermes", name="Bob", type="Agent")
        event = SimpleNamespace(type="contact_added", room_id=None, payload=payload)
        await adapter._handle_event(event)
        evt = adapter.handle_message.call_args[0][0]
        assert "[Contact Added]" in evt.text

    @pytest.mark.asyncio
    async def test_contact_event_dropped_when_hub_not_bootstrapped(self, adapter):
        adapter._hub_room_id = None
        payload = SimpleNamespace(
            id="req-3", from_handle="carol", from_name="Carol",
            message=None, status="pending", inserted_at="x",
        )
        event = SimpleNamespace(type="contact_request_received", room_id=None, payload=payload)
        await adapter._handle_event(event)
        adapter.handle_message.assert_not_called()
```

Now fix the now-stale test in `TestHandleEvent` (around line 859-866). Replace:

```python
    @pytest.mark.asyncio
    async def test_unhandled_event_type_is_ignored(self, adapter):
        # An event the router has no branch for (e.g. a future contact_* event)
        # falls through silently: no raise, no room subscribe/unsubscribe.
        event = SimpleNamespace(type="contact_request_received", room_id="some-room")
        await adapter._handle_event(event)
        adapter._link.subscribe_room.assert_not_called()
        adapter._link.unsubscribe_room.assert_not_called()
```

with:

```python
    @pytest.mark.asyncio
    async def test_unhandled_event_type_is_ignored(self, adapter):
        # An event the router has no branch for falls through silently: no
        # raise, no room subscribe/unsubscribe. contact_* events now have
        # their own branch (see TestContactEvents), so this uses a type the
        # router genuinely doesn't handle.
        event = SimpleNamespace(type="websocket_disconnected", room_id="some-room")
        await adapter._handle_event(event)
        adapter._link.subscribe_room.assert_not_called()
        adapter._link.unsubscribe_room.assert_not_called()
```

Finally, add a `connect()` test. Append to `TestConnectDisconnect` (after `test_connect_returns_true_on_success`, i.e. after line 2341):

```python
    @pytest.mark.asyncio
    async def test_connect_subscribes_to_agent_contacts(self, monkeypatch):
        adapter = _make_adapter(monkeypatch)
        monkeypatch.setattr(
            "gateway.status.acquire_scoped_lock",
            lambda scope, identity, metadata=None: (True, None),
        )
        monkeypatch.setattr(
            "gateway.status.release_scoped_lock",
            lambda scope, identity: None,
        )

        fake_link = MagicMock()
        fake_link.connect = AsyncMock()
        fake_link.subscribe_agent_rooms = AsyncMock()
        fake_link.subscribe_agent_contacts = AsyncMock()
        fake_link.subscribe_room = AsyncMock()
        fake_link.rest.agent_api_identity.get_agent_me = AsyncMock(
            return_value=SimpleNamespace(
                data=SimpleNamespace(id="a1", handle="h1", owner_uuid="o1")
            )
        )
        fake_link.rest.agent_api_chats.list_agent_chats = AsyncMock(
            return_value=SimpleNamespace(data=[], metadata=SimpleNamespace(total_pages=1))
        )
        fake_link.__aiter__ = lambda self: self
        fake_link.__anext__ = AsyncMock(side_effect=StopAsyncIteration)
        monkeypatch.setattr(_band_mod, "BandLink", lambda *a, **kw: fake_link)

        result = await adapter.connect()
        assert result is True
        fake_link.subscribe_agent_contacts.assert_called_once_with(adapter._cfg_agent_id)

        await adapter.disconnect()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_adapter.py -k "ContactEvent or FormatContactEvent or subscribes_to_agent_contacts" -v`
Expected: FAIL — `_format_contact_event` doesn't exist yet; contact events aren't routed; `subscribe_agent_contacts` is never called.

- [ ] **Step 3: Add `_format_contact_event` (pure function)**

In `hermes_band_platform/adapter.py`, add near the existing `_mention_items` function (after its closing, around line 264, before `class _TranscriptRow`):

```python
def _format_contact_event(event: Any) -> Optional[str]:
    """Render a contact_* event as a human-readable line for the owner Hub.

    Mirrors the minimal fields band-sdk's own ``ContactEventHandler`` uses for
    its ``HUB_ROOM`` strategy formatting, without the extra API round-trip
    that strategy does to enrich update events with sender info -- kept
    simple on purpose (id + status is enough for the owner to correlate with
    the original request line). Returns None for an event type this plugin
    doesn't format (never raises).
    """
    etype = getattr(event, "type", None)
    payload = getattr(event, "payload", None)
    if payload is None:
        return None

    if etype == "contact_request_received":
        handle = normalize_handle(getattr(payload, "from_handle", None)) or "?"
        name = getattr(payload, "from_name", None) or "Someone"
        message = getattr(payload, "message", None)
        msg_part = f'\nMessage: "{message}"' if message else ""
        return (
            f"[Contact Request] {name} ({handle}) wants to connect.{msg_part}\n"
            f"Request ID: {getattr(payload, 'id', '?')}"
        )

    if etype == "contact_request_updated":
        return (
            f"[Contact Request Update] Request {getattr(payload, 'id', '?')} "
            f"status changed to: {getattr(payload, 'status', '?')}"
        )

    if etype == "contact_added":
        handle = normalize_handle(getattr(payload, "handle", None)) or "?"
        name = getattr(payload, "name", None) or "Someone"
        return (
            f"[Contact Added] {name} ({handle}) is now a contact.\n"
            f"Type: {getattr(payload, 'type', '?')}, ID: {getattr(payload, 'id', '?')}"
        )

    if etype == "contact_removed":
        return f"[Contact Removed] Contact {getattr(payload, 'id', '?')} was removed."

    return None
```

Add the `normalize_handle` import right after the existing `replace_uuid_mentions` guard (after line 111):

```python
# Reuse the SDK's pure handle-normalizer (ensures a leading "@") rather than
# re-deriving it -- same "reuse the SDK's pure helper, independent fallback"
# idiom as replace_uuid_mentions above.
try:
    from band.runtime.types import normalize_handle
except ImportError:
    def normalize_handle(handle):  # passthrough-with-@ fallback
        if not handle:
            return handle
        return handle if handle.startswith("@") else f"@{handle}"
```

- [ ] **Step 4: Add `_handle_contact_event` and wire it into `_handle_event`**

In `hermes_band_platform/adapter.py`, replace the TODO placeholder in `_handle_event` (currently the last branch, around line 1144):

```python
        # TODO (contacts pass): handle contact_* events. Ignored this release.
```

with:

```python
        if etype in (
            "contact_request_received",
            "contact_request_updated",
            "contact_added",
            "contact_removed",
        ):
            await self._handle_contact_event(event)
            return
```

Then add the `_handle_contact_event` method on `BandAdapter`, right after `_handle_participant_change` (after its closing, around line 1205, before `_handle_message_created`):

```python
    async def _handle_contact_event(self, event: Any) -> None:
        """Format a contact_* event and inject it into the owner Hub session.

        Contact events carry no room_id (band-sdk always sets it to None --
        they are agent-level, not room-scoped), so they are always routed to
        the Hub, the owner's one persistent control room. Dropped (logged
        only) when the Hub isn't bootstrapped yet, mirroring the existing
        fail-closed posture for slash commands when the owner is unresolved.
        """
        if not self._hub_room_id:
            logger.info(
                "[band] Dropping %s -- hub not bootstrapped yet",
                getattr(event, "type", "contact_event"),
            )
            return
        text = _format_contact_event(event)
        if text is None:
            return
        await self.handle_message(
            MessageEvent(
                text=text,
                message_type=MessageType.TEXT,
                source=self.build_source(
                    chat_id=self._hub_room_id, chat_type=_SESSION_CHAT_TYPE
                ),
                internal=True,
                raw_message=getattr(event, "payload", None),
            )
        )
```

- [ ] **Step 5: Subscribe to contact events on connect**

In `hermes_band_platform/adapter.py`, in `connect()` (around line 618), change:

```python
            await self._resolve_identity()
            await self._link.subscribe_agent_rooms(self._cfg_agent_id)
            await self._subscribe_known_rooms()
```

to:

```python
            await self._resolve_identity()
            await self._link.subscribe_agent_rooms(self._cfg_agent_id)
            await self._subscribe_agent_contacts_safe()
            await self._subscribe_known_rooms()
```

Add the new method near `_subscribe_known_rooms` (right after its closing, around line 753):

```python
    async def _subscribe_agent_contacts_safe(self) -> None:
        """Subscribe to contact_* events -- best-effort, never blocks connect().

        Mirrors ``_bootstrap_hub_safe``: a failure here (e.g. an older
        band-sdk without this channel) must not prevent messaging from
        working.
        """
        try:
            await self._link.subscribe_agent_contacts(self._cfg_agent_id)
        except Exception as e:
            logger.warning("[band] Could not subscribe to contact events: %s", e)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_adapter.py -v`
Expected: PASS (all tests, including every pre-existing one — the file's full suite must stay green)

- [ ] **Step 7: Commit**

```bash
git add hermes_band_platform/adapter.py tests/test_adapter.py
git commit -s -m "feat(adapter): route contact_* events into the owner Hub

Subscribes to band-sdk's agent-contacts WebSocket channel on connect
and formats contact_request_received/updated, contact_added, and
contact_removed events as system lines injected into the existing
Hermes Agent Hub session -- replacing the '# TODO (contacts pass)'
placeholder with the deferred handling it named."
```

---

### Task 3: Federation state machine in the adapter

**Files:**
- Modify: `hermes_band_platform/adapter.py` (imports; new `_PendingFederation` dataclass + `FEDERATION_TIMEOUT_SECONDS` constant + `format_federation_digest` pure function; new `BandAdapter` methods: `register_pending_federation`, `_handle_federation_reply`, `_federation_timeout`, `_finalize_federation`; hook in `_handle_message_created`; `_pending_federations` state in `__init__`)
- Test: `tests/test_adapter.py` (new `TestFormatFederationDigest`, `TestFederationStateMachine` classes)

**Interfaces:**
- Consumes: nothing from Tasks 1–2 directly (independent state machine), but reuses the Hub-injection idiom established in Task 2 (`self.handle_message(MessageEvent(..., internal=True))`).
- Produces (consumed by Task 4's `federation.py`):
  - `hermes_band_platform.adapter.FEDERATION_TIMEOUT_SECONDS: int` (300)
  - `hermes_band_platform.adapter._PendingFederation` — dataclass with fields `query: str`, `local_findings: Optional[str]`, `requester_room_id: str`, `expected_agent_ids: List[str]`, `friend_names: Dict[str, str]`, `replies: Dict[str, str]` (default `{}`), `timeout_task: Optional[asyncio.Task]` (default `None`).
  - `BandAdapter.register_pending_federation(self, *, room_id: str, query: str, local_findings: Optional[str], requester_room_id: str, friend_names: Dict[str, str]) -> None`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_adapter.py` (new sections at the end of the file, after `TestHubFailover`):

```python
# ---------------------------------------------------------------------------
# Federated wiki query state machine (federated-wiki-query design, Part 2)
# ---------------------------------------------------------------------------

_PendingFederation = _band_mod._PendingFederation
FEDERATION_TIMEOUT_SECONDS = _band_mod.FEDERATION_TIMEOUT_SECONDS
format_federation_digest = _band_mod.format_federation_digest


class TestFormatFederationDigest:

    def test_all_replied(self):
        pending = _PendingFederation(
            query="what is X?",
            local_findings=None,
            requester_room_id="hub-1",
            expected_agent_ids=["a1", "a2"],
            friend_names={"a1": "Alice", "a2": "Bob"},
            replies={"a1": "X is a widget", "a2": "X is also a gadget"},
        )
        text = format_federation_digest(pending)
        assert "2/2 replies" in text
        assert "Alice: X is a widget" in text
        assert "Bob: X is also a gadget" in text
        assert "Summarize this for the user" in text

    def test_partial_reply_marks_timeout(self):
        pending = _PendingFederation(
            query="what is X?",
            local_findings=None,
            requester_room_id="hub-1",
            expected_agent_ids=["a1", "a2"],
            friend_names={"a1": "Alice", "a2": "Bob"},
            replies={"a1": "X is a widget"},
        )
        text = format_federation_digest(pending)
        assert "1/2 replies" in text
        assert "Alice: X is a widget" in text
        assert "Bob: (no reply, timed out)" in text

    def test_includes_local_findings_when_present(self):
        pending = _PendingFederation(
            query="what is X?",
            local_findings="my wiki says X is a thing",
            requester_room_id="hub-1",
            expected_agent_ids=["a1"],
            friend_names={"a1": "Alice"},
            replies={},
        )
        text = format_federation_digest(pending)
        assert "my wiki says X is a thing" in text

    def test_omits_local_findings_section_when_none(self):
        pending = _PendingFederation(
            query="what is X?",
            local_findings=None,
            requester_room_id="hub-1",
            expected_agent_ids=["a1"],
            friend_names={"a1": "Alice"},
            replies={"a1": "hi"},
        )
        text = format_federation_digest(pending)
        assert "Your own wiki" not in text


class TestFederationStateMachine:

    @pytest.fixture
    def adapter(self, monkeypatch):
        a = _make_adapter(monkeypatch, agent_id="asker-agent")
        a._agent_id = "asker-agent"
        a._link = MagicMock()
        a.handle_message = AsyncMock()
        a._ack_consumed = AsyncMock()
        return a

    def _inbound(self, room_id, sender_id, content, sender_type="Agent", msg_id="m1"):
        return _band_mod._Inbound(
            payload=SimpleNamespace(),
            room_id=room_id,
            msg_id=msg_id,
            content=content,
            message_type="text",
            sender_id=sender_id,
            sender_type=sender_type,
            sender_name="Friend",
        )

    @pytest.mark.asyncio
    async def test_register_pending_federation_schedules_timeout(self, adapter):
        adapter.register_pending_federation(
            room_id="fed-room-1",
            query="what is X?",
            local_findings=None,
            requester_room_id="hub-1",
            friend_names={"a1": "Alice"},
        )
        pending = adapter._pending_federations["fed-room-1"]
        assert pending.query == "what is X?"
        assert pending.expected_agent_ids == ["a1"]
        assert pending.timeout_task is not None
        assert not pending.timeout_task.done()

        pending.timeout_task.cancel()

    @pytest.mark.asyncio
    async def test_reply_from_expected_friend_is_recorded(self, adapter):
        adapter.register_pending_federation(
            room_id="fed-room-2",
            query="what is X?",
            local_findings=None,
            requester_room_id="hub-1",
            friend_names={"a1": "Alice", "a2": "Bob"},
        )
        inb = self._inbound("fed-room-2", "a1", "X is a widget")
        handled = await adapter._handle_federation_reply(inb)

        assert handled is True
        pending = adapter._pending_federations["fed-room-2"]
        assert pending.replies == {"a1": "X is a widget"}
        adapter._ack_consumed.assert_awaited_once_with("fed-room-2", "m1")
        adapter.handle_message.assert_not_called()  # not finalized yet (a2 hasn't replied)

        pending.timeout_task.cancel()

    @pytest.mark.asyncio
    async def test_last_expected_reply_finalizes_and_cancels_timeout(self, adapter):
        adapter.register_pending_federation(
            room_id="fed-room-3",
            query="what is X?",
            local_findings="local hit",
            requester_room_id="hub-1",
            friend_names={"a1": "Alice"},
        )
        timeout_task = adapter._pending_federations["fed-room-3"].timeout_task

        inb = self._inbound("fed-room-3", "a1", "X is a widget")
        handled = await adapter._handle_federation_reply(inb)

        assert handled is True
        assert "fed-room-3" not in adapter._pending_federations  # popped on finalize
        assert timeout_task.cancelled() or timeout_task.done()
        adapter.handle_message.assert_called_once()
        evt = adapter.handle_message.call_args[0][0]
        assert evt.internal is True
        assert evt.source.chat_id == "hub-1"
        assert "1/1 replies" in evt.text
        assert "X is a widget" in evt.text
        assert "local hit" in evt.text

    @pytest.mark.asyncio
    async def test_reply_from_unexpected_sender_is_acked_but_not_recorded(self, adapter):
        adapter.register_pending_federation(
            room_id="fed-room-4",
            query="what is X?",
            local_findings=None,
            requester_room_id="hub-1",
            friend_names={"a1": "Alice"},
        )
        inb = self._inbound("fed-room-4", "not-invited", "hello")
        handled = await adapter._handle_federation_reply(inb)

        assert handled is True
        pending = adapter._pending_federations["fed-room-4"]
        assert pending.replies == {}
        adapter._ack_consumed.assert_awaited_once_with("fed-room-4", "m1")

        pending.timeout_task.cancel()

    @pytest.mark.asyncio
    async def test_non_federation_room_returns_false(self, adapter):
        inb = self._inbound("some-other-room", "a1", "hi")
        handled = await adapter._handle_federation_reply(inb)
        assert handled is False
        adapter.handle_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_timeout_finalizes_with_partial_replies(self, adapter):
        adapter.register_pending_federation(
            room_id="fed-room-5",
            query="what is X?",
            local_findings=None,
            requester_room_id="hub-1",
            friend_names={"a1": "Alice", "a2": "Bob"},
        )
        pending = adapter._pending_federations["fed-room-5"]
        pending.replies["a1"] = "X is a widget"  # a2 never replies

        # Drive the timeout path directly rather than sleeping 5 real minutes.
        await adapter._finalize_federation("fed-room-5", pending)

        assert "fed-room-5" not in adapter._pending_federations
        adapter.handle_message.assert_called_once()
        evt = adapter.handle_message.call_args[0][0]
        assert "1/2 replies" in evt.text
        assert "Bob: (no reply, timed out)" in evt.text

    @pytest.mark.asyncio
    async def test_handle_message_created_intercepts_federation_room(self, adapter, monkeypatch):
        # _handle_message_created must recognize a pending-federation room and
        # bypass normal turn dispatch entirely -- no mention gate, no
        # participant fetch, no forward into a real turn.
        adapter.register_pending_federation(
            room_id="fed-room-6",
            query="what is X?",
            local_findings=None,
            requester_room_id="hub-1",
            friend_names={"a1": "Alice"},
        )
        pending = adapter._pending_federations["fed-room-6"]

        event = SimpleNamespace(
            type="message_created",
            room_id="fed-room-6",
            payload=SimpleNamespace(
                id="m9",
                content="X is a widget",
                message_type="text",
                sender_id="a1",
                sender_type="Agent",
                sender_name="Alice",
                metadata=None,
            ),
        )
        result = await adapter._handle_message_created(event)

        assert result is True
        assert "fed-room-6" not in adapter._pending_federations  # finalized (only friend)
        adapter.handle_message.assert_called_once()  # the digest injection, not a normal turn
        evt = adapter.handle_message.call_args[0][0]
        assert evt.source.chat_id == "hub-1"  # delivered to the requester room, not fed-room-6

        assert pending.timeout_task.cancelled() or pending.timeout_task.done()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_adapter.py -k "Federation" -v`
Expected: FAIL — `_PendingFederation`, `FEDERATION_TIMEOUT_SECONDS`, `format_federation_digest`, `register_pending_federation`, `_handle_federation_reply`, `_finalize_federation` don't exist yet.

- [ ] **Step 3: Add imports, constant, dataclass, and pure formatter**

In `hermes_band_platform/adapter.py`, change the dataclasses import (line 40) from:

```python
from dataclasses import dataclass
```

to:

```python
from dataclasses import dataclass, field
```

Add the timeout constant near the other tunables (after `_HUB_FAILOVER_MAX_PER_CONNECT_DEFAULT`, around line 157):

```python
# How long a federated wiki query waits for friend agents to reply before
# finalizing with whatever came in. Fixed, not env-configurable -- KISS
# per the approved design; add a knob only if a real need for one shows up.
FEDERATION_TIMEOUT_SECONDS = 300
```

Add the `_PendingFederation` dataclass right after `_Inbound` (after its closing, around line 462, before `class BandAdapter`):

```python
@dataclass
class _PendingFederation:
    """State for one in-flight federated wiki query, keyed by its room id.

    Created by ``register_pending_federation`` when ``band_ask_wikis`` posts
    a query; consumed by ``_handle_federation_reply`` (early completion) or
    ``_federation_timeout`` (deadline), whichever fires first.
    """

    query: str
    local_findings: Optional[str]
    requester_room_id: str
    expected_agent_ids: List[str]
    friend_names: Dict[str, str]
    replies: Dict[str, str] = field(default_factory=dict)
    timeout_task: Optional[asyncio.Task] = None


def format_federation_digest(pending: _PendingFederation) -> str:
    """Render the collected federation replies as one prompt for the LLM.

    Every expected friend appears exactly once, in the order they were asked
    (``expected_agent_ids`` preserves insertion order); a friend who never
    replied is shown as "(no reply, timed out)" rather than omitted, so the
    resulting summary can be honest about partial coverage.
    """
    total = len(pending.expected_agent_ids)
    answered = len(pending.replies)
    lines = [
        f'Your federated wiki question "{pending.query}" got {answered}/{total} replies:'
    ]
    for agent_id in pending.expected_agent_ids:
        name = pending.friend_names.get(agent_id, agent_id)
        if agent_id in pending.replies:
            lines.append(f"- {name}: {pending.replies[agent_id]}")
        else:
            lines.append(f"- {name}: (no reply, timed out)")
    if pending.local_findings:
        lines.append("")
        lines.append(f"Your own wiki: {pending.local_findings}")
    lines.append("")
    lines.append("Summarize this for the user in a clear, concise answer.")
    return "\n".join(lines)
```

- [ ] **Step 4: Add `_pending_federations` state to `__init__`**

In `hermes_band_platform/adapter.py`, in `BandAdapter.__init__`, right after the `# ── Per-room caches ──` block (after line 537, `self._last_human_sender: Dict[str, Dict[str, Any]] = {}`):

```python
        # ── Federated wiki queries ──
        # In-flight band_ask_wikis calls, keyed by the fresh room created for
        # each query. In-memory only (resets on restart), same caveat as
        # _last_human_sender / _seen_inbound_ids.
        self._pending_federations: Dict[str, _PendingFederation] = {}
```

- [ ] **Step 5: Add the adapter methods**

In `hermes_band_platform/adapter.py`, add these methods on `BandAdapter`, placed right after `_handle_contact_event` (added in Task 2, around line 1230):

```python
    def register_pending_federation(
        self,
        *,
        room_id: str,
        query: str,
        local_findings: Optional[str],
        requester_room_id: str,
        friend_names: Dict[str, str],
    ) -> None:
        """Track a just-created federation room until every friend replies or timeout.

        Called by the ``band_ask_wikis`` tool right after it posts the query
        into ``room_id``. The timeout task is scheduled here (not in the
        tool) so it keeps running after the tool call itself returns.
        """
        pending = _PendingFederation(
            query=query,
            local_findings=local_findings,
            requester_room_id=requester_room_id,
            expected_agent_ids=list(friend_names.keys()),
            friend_names=dict(friend_names),
        )
        pending.timeout_task = asyncio.create_task(self._federation_timeout(room_id))
        self._pending_federations[room_id] = pending

    async def _federation_timeout(self, room_id: str) -> None:
        """Finalize a federation query if it's still pending after the deadline."""
        try:
            await asyncio.sleep(FEDERATION_TIMEOUT_SECONDS)
        except asyncio.CancelledError:
            return
        pending = self._pending_federations.get(room_id)
        if pending is None:
            return  # already finalized by _handle_federation_reply
        await self._finalize_federation(room_id, pending)

    async def _handle_federation_reply(self, inb: "_Inbound") -> bool:
        """Record one friend's reply to a pending federation query.

        Returns True whether or not the reply was recorded, so the caller
        (``_handle_message_created``) never falls through to normal turn
        dispatch for a federation room -- friends' replies must not make
        this agent chatter back into the round-table.
        """
        pending = self._pending_federations.get(inb.room_id)
        if pending is None:
            return False
        if (
            inb.sender_type == "Agent"
            and inb.sender_id in pending.expected_agent_ids
            and inb.sender_id not in pending.replies
        ):
            pending.replies[inb.sender_id] = inb.content
            if len(pending.replies) >= len(pending.expected_agent_ids):
                if pending.timeout_task and not pending.timeout_task.done():
                    pending.timeout_task.cancel()
                await self._finalize_federation(inb.room_id, pending)
        await self._ack_consumed(inb.room_id, inb.msg_id)
        return True

    async def _finalize_federation(self, room_id: str, pending: _PendingFederation) -> None:
        """Deliver one synthesized-answer prompt to wherever the query was asked from."""
        self._pending_federations.pop(room_id, None)
        digest = format_federation_digest(pending)
        await self.handle_message(
            MessageEvent(
                text=digest,
                message_type=MessageType.TEXT,
                source=self.build_source(
                    chat_id=pending.requester_room_id, chat_type=_SESSION_CHAT_TYPE
                ),
                internal=True,
            )
        )
```

- [ ] **Step 6: Hook the intercept into `_handle_message_created`**

In `hermes_band_platform/adapter.py`, in `_handle_message_created`, find this block (around line 1229-1237):

```python
        # Only conversational text reaches the agent — tool/thought/task etc. are
        # event rows, and /next returns text only, so these never appear in a drain.
        if inb.message_type and inb.message_type != "text":
            logger.debug(
                "[band] Skipping non-text message_type=%s in room %s",
                inb.message_type,
                _short_id(inb.room_id),
            )
            return False
```

and insert immediately after it (before the `# Participants drive @mention resolution...` comment that currently follows):

```python
        # A reply in an in-flight federation room is handled entirely by the
        # state machine (record + maybe finalize) -- it must never go through
        # normal mention-gating/dispatch, or this agent would start chattering
        # back into the round-table after every friend's reply.
        if inb.room_id in self._pending_federations:
            return await self._handle_federation_reply(inb)
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest tests/test_adapter.py -v`
Expected: PASS (all tests, including every pre-existing one)

- [ ] **Step 8: Commit**

```bash
git add hermes_band_platform/adapter.py tests/test_adapter.py
git commit -s -m "feat(adapter): add federated wiki query state machine

Tracks an in-flight band_ask_wikis query per room (_PendingFederation),
records friend replies via an early intercept in
_handle_message_created, and finalizes -- on full completion or a
5-minute timeout -- by injecting one synthesized-answer prompt back
into wherever the query was originally asked from."
```

---

### Task 4: The `band_ask_wikis` tool (`federation.py`)

**Files:**
- Create: `hermes_band_platform/federation.py`
- Create: `hermes_band_platform/skills/federated-wiki-search/SKILL.md`
- Modify: `hermes_band_platform/adapter.py:2578-2628` (`register()` — register the new tool + skill)
- Test: `tests/test_federation.py`
- Test: `tests/test_adapter.py` (append registration assertions)

**Interfaces:**
- Consumes: `hermes_band_platform.adapter.FEDERATION_TIMEOUT_SECONDS`, `hermes_band_platform.adapter._PendingFederation` (for type-checking test fixtures only — the tool itself only calls `adapter.register_pending_federation(...)`), plus `tools.py`'s `_rest`, `_tool_exc`, `_ToolError`, `_ToolUnavailable`, `_authorize_band_action`.
- Produces: `FEDERATION_TOOLS` — a tuple of `(name, schema, handler, emoji)`, consumed by `register()` in `adapter.py`. Handler: `_handle_ask_wikis`. Also produces `_live_band_adapter() -> Optional[Any]`, a new small helper added to `tools.py` in Step 3 that both `federation.py` and (potentially) future modules can use to get the live `BandAdapter` instance itself, not just its REST client.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_federation.py`:

```python
"""Tests for the federated wiki query tool (``hermes_band_platform/federation.py``).

Strategy mirrors ``tests/test_tools.py``: the band-sdk request types
(``ChatRoomRequest`` / ``ParticipantRequest`` / ``ChatMessageRequest`` /
``ChatMessageRequestMentionsItem``) bind from the ``sys.modules`` stub
installed by ``tests/conftest.py``, so real request objects are constructed.
``federation._rest`` and ``federation._live_band_adapter`` are patched to
avoid a live gateway/network, and ``federation.ContactTools`` is patched
the same way ``tests/test_contacts.py`` patches ``contacts.ContactTools``.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway.session_context import set_session_vars, clear_session_vars
from hermes_band_platform import federation as band_federation


def _make_rest() -> MagicMock:
    rest = MagicMock()
    rest.agent_api_chats.create_agent_chat = AsyncMock(
        return_value=SimpleNamespace(data=SimpleNamespace(id="fed-room-001"))
    )
    rest.agent_api_participants.add_agent_chat_participant = AsyncMock(
        return_value=SimpleNamespace(data=SimpleNamespace(id="part-001"))
    )
    rest.agent_api_messages.create_agent_chat_message = AsyncMock(
        return_value=SimpleNamespace(data=SimpleNamespace(id="msg-001"))
    )
    return rest


def _make_contact_tools(contacts):
    fake = MagicMock()
    fake.list_contacts = AsyncMock(return_value={"contacts": contacts, "metadata": {}})
    return fake


def _agent_contact(cid, handle, name):
    return {"id": cid, "handle": handle, "name": name, "type": "Agent"}


def _human_contact(cid, handle, name):
    return {"id": cid, "handle": handle, "name": name, "type": "User"}


def _fake_adapter():
    a = MagicMock()
    a._hub_room_id = "hub-1"
    a.register_pending_federation = MagicMock()
    return a


def _parse(result: str) -> dict:
    assert isinstance(result, str)
    return json.loads(result)


@pytest.fixture
def owner_session():
    tokens = set_session_vars(platform="band", chat_id="room-current", user_id="u-owner")
    try:
        yield
    finally:
        clear_session_vars(tokens)


@pytest.fixture(autouse=True)
def _owner_env(monkeypatch):
    monkeypatch.setenv("BAND_TOOL_OWNERS", "band:u-owner")
    yield


class TestAskWikisDefaultFanOut:

    @pytest.mark.asyncio
    async def test_asks_every_agent_contact_by_default(self, owner_session):
        rest = _make_rest()
        contacts = [
            _agent_contact("a1", "alice/hermes", "Alice"),
            _agent_contact("a2", "bob/hermes", "Bob"),
            _human_contact("h1", "carol", "Carol"),  # excluded: not an agent
        ]
        adapter = _fake_adapter()
        with patch.object(band_federation, "_rest", AsyncMock(return_value=rest)), \
             patch.object(band_federation, "_live_band_adapter", return_value=adapter), \
             patch.object(band_federation, "ContactTools", MagicMock(return_value=_make_contact_tools(contacts))):
            out = _parse(await band_federation._handle_ask_wikis({"query": "what is X?"}))

        assert out["success"] is True
        assert out["room_id"] == "fed-room-001"
        assert sorted(out["asked"]) == ["Alice", "Bob"]
        assert out["timeout_seconds"] == band_federation.FEDERATION_TIMEOUT_SECONDS
        assert rest.agent_api_participants.add_agent_chat_participant.await_count == 2
        rest.agent_api_messages.create_agent_chat_message.assert_awaited_once()

        adapter.register_pending_federation.assert_called_once()
        kwargs = adapter.register_pending_federation.call_args.kwargs
        assert kwargs["room_id"] == "fed-room-001"
        assert kwargs["query"] == "what is X?"
        assert kwargs["requester_room_id"] == "room-current"
        assert set(kwargs["friend_names"].keys()) == {"a1", "a2"}

    @pytest.mark.asyncio
    async def test_fails_fast_with_no_agent_contacts(self, owner_session):
        adapter = _fake_adapter()
        with patch.object(band_federation, "_rest", AsyncMock(return_value=_make_rest())), \
             patch.object(band_federation, "_live_band_adapter", return_value=adapter), \
             patch.object(band_federation, "ContactTools", MagicMock(return_value=_make_contact_tools([]))):
            out = _parse(await band_federation._handle_ask_wikis({"query": "what is X?"}))
        assert "error" in out
        adapter.register_pending_federation.assert_not_called()

    @pytest.mark.asyncio
    async def test_requires_query(self, owner_session):
        out = _parse(await band_federation._handle_ask_wikis({}))
        assert "error" in out


class TestAskWikisNamedFriends:

    @pytest.mark.asyncio
    async def test_narrows_to_named_friends(self, owner_session):
        rest = _make_rest()
        contacts = [
            _agent_contact("a1", "alice/hermes", "Alice"),
            _agent_contact("a2", "bob/hermes", "Bob"),
        ]
        adapter = _fake_adapter()
        with patch.object(band_federation, "_rest", AsyncMock(return_value=rest)), \
             patch.object(band_federation, "_live_band_adapter", return_value=adapter), \
             patch.object(band_federation, "ContactTools", MagicMock(return_value=_make_contact_tools(contacts))):
            out = _parse(
                await band_federation._handle_ask_wikis(
                    {"query": "what is X?", "friends": ["alice/hermes"]}
                )
            )
        assert out["success"] is True
        assert out["asked"] == ["Alice"]
        kwargs = adapter.register_pending_federation.call_args.kwargs
        assert set(kwargs["friend_names"].keys()) == {"a1"}

    @pytest.mark.asyncio
    async def test_warns_on_unresolved_friend(self, owner_session):
        rest = _make_rest()
        contacts = [_agent_contact("a1", "alice/hermes", "Alice")]
        adapter = _fake_adapter()
        with patch.object(band_federation, "_rest", AsyncMock(return_value=rest)), \
             patch.object(band_federation, "_live_band_adapter", return_value=adapter), \
             patch.object(band_federation, "ContactTools", MagicMock(return_value=_make_contact_tools(contacts))):
            out = _parse(
                await band_federation._handle_ask_wikis(
                    {"query": "what is X?", "friends": ["alice/hermes", "ghost"]}
                )
            )
        assert out["success"] is True
        assert "ghost" in out["warning"]

    @pytest.mark.asyncio
    async def test_fails_when_no_named_friend_resolves(self, owner_session):
        rest = _make_rest()
        contacts = [_agent_contact("a1", "alice/hermes", "Alice")]
        adapter = _fake_adapter()
        with patch.object(band_federation, "_rest", AsyncMock(return_value=rest)), \
             patch.object(band_federation, "_live_band_adapter", return_value=adapter), \
             patch.object(band_federation, "ContactTools", MagicMock(return_value=_make_contact_tools(contacts))):
            out = _parse(
                await band_federation._handle_ask_wikis(
                    {"query": "what is X?", "friends": ["ghost"]}
                )
            )
        assert "error" in out
        adapter.register_pending_federation.assert_not_called()


class TestAskWikisRequesterRoom:

    @pytest.mark.asyncio
    async def test_falls_back_to_hub_outside_band_session(self):
        rest = _make_rest()
        contacts = [_agent_contact("a1", "alice/hermes", "Alice")]
        adapter = _fake_adapter()
        with patch.object(band_federation, "_rest", AsyncMock(return_value=rest)), \
             patch.object(band_federation, "_live_band_adapter", return_value=adapter), \
             patch.object(band_federation, "ContactTools", MagicMock(return_value=_make_contact_tools(contacts))):
            out = _parse(await band_federation._handle_ask_wikis({"query": "what is X?"}))
        assert out["success"] is True
        kwargs = adapter.register_pending_federation.call_args.kwargs
        assert kwargs["requester_room_id"] == "hub-1"

    @pytest.mark.asyncio
    async def test_errors_when_no_room_and_no_hub(self):
        rest = _make_rest()
        contacts = [_agent_contact("a1", "alice/hermes", "Alice")]
        adapter = _fake_adapter()
        adapter._hub_room_id = None
        with patch.object(band_federation, "_rest", AsyncMock(return_value=rest)), \
             patch.object(band_federation, "_live_band_adapter", return_value=adapter), \
             patch.object(band_federation, "ContactTools", MagicMock(return_value=_make_contact_tools(contacts))):
            out = _parse(await band_federation._handle_ask_wikis({"query": "what is X?"}))
        assert "error" in out


class TestAskWikisNoLiveAdapter:

    @pytest.mark.asyncio
    async def test_errors_when_adapter_not_running(self, owner_session):
        with patch.object(band_federation, "_live_band_adapter", return_value=None):
            out = _parse(await band_federation._handle_ask_wikis({"query": "what is X?"}))
        assert "error" in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_federation.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'hermes_band_platform.federation'`

- [ ] **Step 3: Add `_live_band_adapter` to `tools.py`**

In `hermes_band_platform/tools.py`, add this helper right after `_rest()` (after its closing, around line 183):

```python
def _live_band_adapter() -> Optional[Any]:
    """Return the live BandAdapter instance from the running gateway, or None.

    ``_rest()`` only returns the link's REST client; callers that need
    adapter-level state (e.g. ``federation.py`` calling
    ``register_pending_federation``) need the adapter object itself. Mirrors
    the same runner-lookup pattern ``_rest()`` / ``_owner_identity()`` /
    ``_home_room()`` already use inline.
    """
    try:
        from gateway.run import _gateway_runner_ref

        runner = _gateway_runner_ref()
        return runner.adapters.get(Platform("band")) if runner else None
    except Exception:
        return None
```

- [ ] **Step 4: Create `hermes_band_platform/federation.py`**

```python
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_federation.py -v`
Expected: PASS (10 tests)

- [ ] **Step 6: Create the `band:federated-wiki-search` skill**

Create `hermes_band_platform/skills/federated-wiki-search/SKILL.md`:

```markdown
---
name: federated-wiki-search
description: "Use when the user wants every connected Hermes friend's wiki to answer the same question -- phrases like 'ask all my wikis about X', 'federated wiki search for X', 'search across my federation for X', 'what do all my agents know about X', 'ask my friends' wikis about X'. Also covers the OTHER side: how to answer when a friend's agent asks YOU a federated wiki question."
version: 1.0.0
metadata:
  hermes:
    tags: [band, wiki, federation, multi-agent]
    requires_tools: [band_ask_wikis]
---

# Federated wiki search

Combines a **local** wiki lookup with a **federated** broadcast to your
connected Hermes friends, and reports back a single consolidated answer once
their agents have had a chance to reply.

## Asking: "ask all my wikis about X"

1. **Search your own wiki first**, using Hermes' bundled `research-llm-wiki`
   skill workflow, for the same question. That skill composes generic
   file/search tools -- it does not register one callable named
   `wiki_search`. Capture a short summary of what you found (or that you
   found nothing -- that's a useful, explicit answer too).

2. **Call `band_ask_wikis`**, passing your local summary as `local_findings`:

   ```
   band_ask_wikis(query=<the user's question, verbatim>, local_findings=<your summary>)
   ```

   Omit `friends` to ask every connected agent-type contact; pass a list of
   handles/names/ids to narrow it to specific friends.

3. **Tell the user it's in flight.** The tool returns immediately with
   `room_id`, who was asked, and the timeout. You do NOT get replies back
   synchronously -- do not wait, poll, or call the tool again for the same
   question. A follow-up message with the consolidated answer arrives on its
   own, automatically, once every friend has replied or the timeout passes.
   When that follow-up prompt appears (it will say `Summarize this for the
   user...`), that is your cue to answer the user -- don't ignore it as
   unrelated context.

4. If `band_ask_wikis` returns an error (e.g. no agent contacts yet), tell
   the user and suggest `band_add_contact` to connect with a friend's Hermes
   agent first.

## Answering: a friend's agent asks YOU a federated wiki question

You'll recognize this because you're mentioned in a room together with the
question, typically alongside other friend agents (a round-table), and
nobody in the room is a human.

1. Search your own wiki for the question, via `research-llm-wiki`, same as above.
2. Reply with `band_send_message`, and **explicitly pass `mention_ids`
   covering every other participant in the room** (call `band_get_participants`
   first to get their ids). Do NOT rely on `band_send_message`'s default
   mention behavior -- it only mentions non-agent participants, which would
   silently exclude the agent who asked you, and your answer would never
   reach them.
3. Answer once. Don't loop back and re-answer if you see more traffic in the
   same room afterward unless directly asked something new.

## Anti-patterns

- **Waiting or polling after calling `band_ask_wikis`.** The state machine
  (not you) tracks replies and finalizes automatically; calling the tool
  again for the same question just opens a second, redundant round-table.
- **Skipping the local wiki step when asking.** The whole value of this
  skill over a bare federated broadcast is that the user gets their own
  wiki's findings folded into the one final answer.
- **Replying without explicit `mention_ids` when answering.** Your reply
  would go unmentioned to the asking agent and never be counted.
```

- [ ] **Step 7: Register the tool and skill in `register()`**

In `hermes_band_platform/adapter.py`, in `register()`, extend the same block from Task 1's Step 6:

```python
    from . import contacts as _band_contacts
    from . import federation as _band_federation
    from . import tools as _band_tools

    for name, schema, handler, emoji in _band_tools.BAND_TOOLS:
        ctx.register_tool(
            name=name,
            toolset="band",
            schema=schema,
            handler=handler,
            check_fn=_band_tools._check_band_tools_available,
            is_async=True,
            emoji=emoji,
        )

    for name, schema, handler, emoji in _band_contacts.CONTACT_TOOLS:
        ctx.register_tool(
            name=name,
            toolset="band",
            schema=schema,
            handler=handler,
            check_fn=_band_tools._check_band_tools_available,
            is_async=True,
            emoji=emoji,
        )

    for name, schema, handler, emoji in _band_federation.FEDERATION_TOOLS:
        ctx.register_tool(
            name=name,
            toolset="band",
            schema=schema,
            handler=handler,
            check_fn=_band_tools._check_band_tools_available,
            is_async=True,
            emoji=emoji,
        )
```

And add the skill registration right after the `band-contacts` block added in Task 1 (still inside the same `try:`):

```python
        _federated_wiki_md = (
            _SkillPath(__file__).parent / "skills" / "federated-wiki-search" / "SKILL.md"
        )
        if _federated_wiki_md.exists():
            ctx.register_skill(
                "federated-wiki-search",
                _federated_wiki_md,
                description=(
                    "Federate an LLM-wiki question to connected Hermes friends' "
                    "agents and get one consolidated answer back."
                ),
            )
```

- [ ] **Step 8: Add registration assertions**

Append to `tests/test_adapter.py`'s `TestContactToolRegistration` class (added in Task 1), or add a sibling class right after it:

```python
class TestFederationToolRegistration:

    def test_register_registers_ask_wikis_tool(self):
        ctx = MagicMock()
        register(ctx)
        names = {c.kwargs["name"] for c in ctx.register_tool.call_args_list}
        assert "band_ask_wikis" in names

    def test_register_registers_federated_wiki_search_skill(self):
        ctx = MagicMock()
        register(ctx)
        skill_names = {c.args[0] for c in ctx.register_skill.call_args_list}
        assert "federated-wiki-search" in skill_names
```

- [ ] **Step 9: Run the full suite to verify no regressions**

Run: `pytest tests/ -v`
Expected: PASS — every test in `tests/test_adapter.py`, `tests/test_tools.py`, `tests/test_contacts.py`, `tests/test_federation.py`, and the pre-existing `tests/test_add_band_skill.py` / `tests/test_package_layout.py`.

- [ ] **Step 10: Commit**

```bash
git add hermes_band_platform/federation.py hermes_band_platform/skills/federated-wiki-search/SKILL.md hermes_band_platform/adapter.py hermes_band_platform/tools.py tests/test_federation.py tests/test_adapter.py
git commit -s -m "feat(federation): add band_ask_wikis tool and skill

Opens a fresh room per query, resolves target friends (default: every
agent-type contact), posts the question, and registers it with the
adapter's federation state machine. Adds the federated-wiki-search
skill covering both the asking and answering sides."
```

---

### Task 5: Full verification pass

**Files:** none created/modified — verification only.

**Interfaces:** none.

- [ ] **Step 1: Run the entire test suite**

Run: `cd /Users/ofer/dev/band/hermes-band-platform && pytest tests/ -v`
Expected: PASS, 0 failures. Note the total test count in your final report.

- [ ] **Step 2: Run ruff (the project's configured linter)**

Run: `ruff check hermes_band_platform/`
Expected: no errors. If ruff flags anything in the 4 new/modified files (`contacts.py`, `federation.py`, `adapter.py`, `tools.py`), fix it and re-run.

- [ ] **Step 3: Verify the plugin still imports cleanly without band-sdk**

Run:
```bash
python3 -c "
import sys
import types
# Simulate band-sdk being entirely absent.
for mod in list(sys.modules):
    if mod == 'band' or mod.startswith('band.'):
        del sys.modules[mod]
sys.modules['band'] = None  # force ImportError on any 'import band*'
import importlib
try:
    import hermes_band_platform
    print('OK: plugin package imports without band-sdk present')
except Exception as e:
    print('FAIL:', e)
    raise
"
```
Expected: `OK: plugin package imports without band-sdk present`. (This checks the invariant stated in the Global Constraints — every new module's lazy-import guard must degrade gracefully.)

- [ ] **Step 4: Confirm every commit on the branch is DCO-signed**

Run: `git log --pretty='%h %s%n%b' main..HEAD | grep -c '^Signed-off-by:'`
Expected: equals the number of commits made across Tasks 1–4 (4 commits, assuming this plan's commits are the only ones added on top of `main`). If any commit is missing a sign-off, fix it with `git commit --amend -s --no-edit` targeting that commit via an interactive-free rebase, or ask the user how they'd like it handled.

- [ ] **Step 5: Report completion**

Run `pytest tests/ --collect-only -q | tail -1` to get the exact total test count. Summarize for the user: the 4 new/changed files (`contacts.py`, `federation.py`, `adapter.py`, `tools.py`) plus the 2 new skill files and 2 new test files, the exact new-test count from the collect-only run, and confirm the full suite is green.
