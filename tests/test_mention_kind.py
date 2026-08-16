"""Inbound addressing honours the platform's mention kinds.

The platform distinguishes two kinds (``chat.ex`` ``@valid_mention_kinds
~w(mention reference)``). Every server path that decides whether an agent should
*act* gates on ``delivery_mention?/1`` — ``kind == "mention"``. A ``reference``
names someone narratively without asking anything of them.

The gateway has to make the same judgement wherever it re-derives addressedness
for itself rather than being handed it — backlog enumeration and rehydration —
or it wakes turns the server deliberately never offered.

``mention_kind/1`` defaults to ``"mention"`` when the key is absent, so a
kindless entry is a delivery mention. Getting that default wrong in the other
direction would silently mute legacy messages, which is why it is pinned here.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from hermes_band_platform.adapter import BandAdapter, _is_delivery_mention

SELF_ID = "agent-self-id"
SELF_HANDLE = "owner/self"
PEER_ID = "agent-peer-id"
ROOM = "room-abc"


def _make_adapter(monkeypatch):
    from gateway.config import PlatformConfig

    monkeypatch.setenv("BAND_AGENT_ID", SELF_ID)
    monkeypatch.setenv("BAND_API_KEY", "secret-key")
    monkeypatch.delenv("BAND_BASE_URL", raising=False)
    monkeypatch.delenv("BAND_OWNER_ID", raising=False)
    a = BandAdapter(PlatformConfig(enabled=True, extra={}))
    a._agent_id = SELF_ID
    a._handle = SELF_HANDLE
    return a


@pytest.fixture
def adapter(monkeypatch):
    return _make_adapter(monkeypatch)


def _payload(mentions, as_dict=False):
    if as_dict:
        return SimpleNamespace(metadata={"mentions": mentions})
    return SimpleNamespace(
        metadata=SimpleNamespace(
            mentions=[SimpleNamespace(**m) for m in mentions]
        )
    )


class TestDeliveryMentionPredicate:
    @pytest.mark.parametrize(
        "kind,expected",
        [
            (None, True),            # legacy rows carry no kind
            ("mention", True),
            ("Mention", True),       # tolerate casing
            ("  mention  ", True),   # and whitespace
            ("", True),              # empty is not an explicit reference
            ("reference", False),
            ("REFERENCE", False),
        ],
    )
    def test_kind_values(self, kind, expected):
        assert _is_delivery_mention({"id": "x", "kind": kind}) is expected

    def test_missing_key_is_a_delivery_mention(self):
        assert _is_delivery_mention({"id": "x"}) is True

    def test_object_shape(self):
        assert _is_delivery_mention(SimpleNamespace(id="x", kind="reference")) is False
        assert _is_delivery_mention(SimpleNamespace(id="x")) is True


class TestIsAgentMentioned:
    def test_delivery_mention_of_this_agent_addresses_it(self, adapter):
        assert adapter._is_agent_mentioned(
            _payload([{"id": SELF_ID, "kind": "mention"}])
        )

    def test_kindless_mention_still_addresses_it(self, adapter):
        """Legacy metadata must not go silently unanswered."""
        assert adapter._is_agent_mentioned(_payload([{"id": SELF_ID}]))

    def test_reference_to_this_agent_does_not_address_it(self, adapter):
        assert not adapter._is_agent_mentioned(
            _payload([{"id": SELF_ID, "kind": "reference"}])
        )

    def test_reference_by_handle_does_not_address_it(self, adapter):
        assert not adapter._is_agent_mentioned(
            _payload([{"id": None, "handle": SELF_HANDLE, "kind": "reference"}])
        )

    def test_delivery_mention_by_handle_addresses_it(self, adapter):
        assert adapter._is_agent_mentioned(
            _payload([{"id": None, "handle": SELF_HANDLE, "kind": "mention"}])
        )

    def test_a_reference_alongside_someone_elses_delivery_mention(self, adapter):
        """'as @self noted' while actually addressing a peer — not our turn."""
        assert not adapter._is_agent_mentioned(
            _payload(
                [
                    {"id": SELF_ID, "kind": "reference"},
                    {"id": PEER_ID, "kind": "mention"},
                ]
            )
        )

    def test_a_delivery_mention_survives_an_unrelated_reference(self, adapter):
        assert adapter._is_agent_mentioned(
            _payload(
                [
                    {"id": PEER_ID, "kind": "reference"},
                    {"id": SELF_ID, "kind": "mention"},
                ]
            )
        )

    def test_dict_metadata_shape_is_handled(self, adapter):
        """The caught-up PlatformMessage shape, used by backlog enumeration."""
        assert not adapter._is_agent_mentioned(
            _payload([{"id": SELF_ID, "kind": "reference"}], as_dict=True)
        )
        assert adapter._is_agent_mentioned(
            _payload([{"id": SELF_ID}], as_dict=True)
        )


class TestNoTurnIsRun:
    """The property that matters: a reference must not cost a model turn."""

    @pytest.fixture
    def wired(self, monkeypatch):
        a = _make_adapter(monkeypatch)
        a.handle_message = AsyncMock()
        a._ack_consumed = AsyncMock()
        a._participants_cache[ROOM] = [
            {"id": SELF_ID, "type": "Agent", "name": "Bot", "handle": SELF_HANDLE},
            {"id": PEER_ID, "type": "Agent", "name": "Peer", "handle": "owner/peer"},
        ]
        return a

    def _event(self, kind, msg_id):
        payload = SimpleNamespace(
            id=msg_id,
            content="as noted earlier, the cap is per adapter",
            message_type="text",
            sender_id=PEER_ID,
            sender_type="Agent",
            sender_name="Peer",
            chat_room_id=ROOM,
            metadata=SimpleNamespace(
                mentions=[SimpleNamespace(id=SELF_ID, handle=None, kind=kind)]
            ),
        )
        return SimpleNamespace(type="message_created", room_id=ROOM, payload=payload)

    async def test_a_reference_does_not_wake_a_turn(self, wired):
        await wired._handle_message_created(self._event("reference", "m-1"))

        wired.handle_message.assert_not_awaited()

    async def test_a_delivery_mention_does_wake_a_turn(self, wired):
        await wired._handle_message_created(self._event("mention", "m-2"))

        wired.handle_message.assert_awaited_once()

    async def test_a_kindless_mention_does_wake_a_turn(self, wired):
        await wired._handle_message_created(self._event(None, "m-3"))

        wired.handle_message.assert_awaited_once()

