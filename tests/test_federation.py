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
    async def test_falls_back_to_hub_outside_band_session(self, monkeypatch):
        # No Band session identity is established in this test, so leave the
        # BAND_TOOL_OWNERS allowlist unset (loose default) -- this test is
        # about _resolve_requester_room's hub fallback, not authorization.
        # Mirrors the same "loose default" pattern tests/test_tools.py uses
        # for scenarios where authorization is not what's under test.
        monkeypatch.delenv("BAND_TOOL_OWNERS", raising=False)
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
    async def test_errors_when_no_room_and_no_hub(self, monkeypatch):
        monkeypatch.delenv("BAND_TOOL_OWNERS", raising=False)
        rest = _make_rest()
        contacts = [_agent_contact("a1", "alice/hermes", "Alice")]
        adapter = _fake_adapter()
        adapter._hub_room_id = None
        with patch.object(band_federation, "_rest", AsyncMock(return_value=rest)), \
             patch.object(band_federation, "_live_band_adapter", return_value=adapter), \
             patch.object(band_federation, "ContactTools", MagicMock(return_value=_make_contact_tools(contacts))):
            out = _parse(await band_federation._handle_ask_wikis({"query": "what is X?"}))
        assert "error" in out
        assert "no room to deliver" in out["error"]


class TestAskWikisNoLiveAdapter:

    @pytest.mark.asyncio
    async def test_errors_when_adapter_not_running(self, owner_session):
        with patch.object(band_federation, "_live_band_adapter", return_value=None):
            out = _parse(await band_federation._handle_ask_wikis({"query": "what is X?"}))
        assert "error" in out
