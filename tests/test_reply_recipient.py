"""Who an automatic reply addresses.

Band is mention-gated: a participant receives a message only if it @mentions
them. So the recipient of the automatic reply is not a cosmetic choice — address
the wrong participant and the one who asked never sees the answer, while Band
reports the send as successful and nothing anywhere records a failure.

These tests pin that the reply goes to whoever last addressed the agent.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from hermes_band_platform.adapter import BandAdapter

SELF_ID = "agent-self-id"
PEER_ID = "agent-peer-id"
HUMAN_ID = "human-sender"
ROOM = "room-abc"


@pytest.fixture
def adapter(monkeypatch):
    from gateway.config import PlatformConfig

    monkeypatch.setenv("BAND_AGENT_ID", SELF_ID)
    monkeypatch.setenv("BAND_API_KEY", "secret-key")
    monkeypatch.delenv("BAND_BASE_URL", raising=False)
    monkeypatch.delenv("BAND_OWNER_ID", raising=False)
    a = BandAdapter(PlatformConfig(enabled=True, extra={}))
    a._agent_id = SELF_ID
    a.handle_message = AsyncMock()
    a._ack_consumed = AsyncMock()
    a._participants_cache[ROOM] = [
        {"id": SELF_ID, "type": "Agent", "name": "Bot", "handle": "owner/bot"},
        {"id": PEER_ID, "type": "Agent", "name": "Peer", "handle": "owner/peer"},
        {"id": HUMAN_ID, "type": "User", "name": "Alice", "handle": "alice"},
    ]
    return a


_ids = iter(range(1, 10_000))


def _event(sender_id, sender_type, room_id=ROOM):
    payload = SimpleNamespace(
        id=f"msg-{next(_ids):04d}",
        content="hello",
        message_type="text",
        sender_id=sender_id,
        sender_type=sender_type,
        sender_name="Sender",
        chat_room_id=room_id,
        metadata=SimpleNamespace(mentions=[SimpleNamespace(id=SELF_ID, handle=None)]),
    )
    return SimpleNamespace(type="message_created", room_id=room_id, payload=payload)


class TestReplyAddressesTheAsker:
    async def test_reply_goes_to_the_peer_agent_that_asked(self, adapter):
        """The regression: a human spoke first, then an agent asked."""
        await adapter._handle_message_created(_event(HUMAN_ID, "User"))
        await adapter._handle_message_created(_event(PEER_ID, "Agent"))

        items = await adapter._build_mentions(ROOM)

        # Previously this returned the human, so the asking agent — which is
        # mention-gated like everyone else — never received the answer.
        assert [i.id for i in items] == [PEER_ID]

    async def test_reply_goes_to_the_human_that_asked(self, adapter):
        await adapter._handle_message_created(_event(PEER_ID, "Agent"))
        await adapter._handle_message_created(_event(HUMAN_ID, "User"))

        items = await adapter._build_mentions(ROOM)

        assert [i.id for i in items] == [HUMAN_ID]

    async def test_the_recipient_tracks_the_most_recent_asker(self, adapter):
        for sender, sender_type in [
            (HUMAN_ID, "User"),
            (PEER_ID, "Agent"),
            (HUMAN_ID, "User"),
            (PEER_ID, "Agent"),
        ]:
            await adapter._handle_message_created(_event(sender, sender_type))
            expected = sender
            items = await adapter._build_mentions(ROOM)
            assert [i.id for i in items] == [expected]

    async def test_the_recipient_carries_the_handle_for_in_place_rendering(
        self, adapter
    ):
        """The platform substitutes @handle into content when it is supplied."""
        await adapter._handle_message_created(_event(PEER_ID, "Agent"))

        items = await adapter._build_mentions(ROOM)

        assert items[0].handle == "owner/peer"

    async def test_the_agent_is_never_its_own_recipient(self, adapter):
        # Self-sent messages are filtered before any sender tracking runs.
        await adapter._handle_message_created(_event(SELF_ID, "Agent"))

        assert ROOM not in adapter._last_sender


class TestFallbacks:
    async def test_falls_back_to_last_human_when_no_sender_is_tracked(self, adapter):
        """A reconnect can leave the older cache populated and the newer empty."""
        adapter._last_human_sender[ROOM] = {
            "id": HUMAN_ID,
            "handle": "alice",
            "name": "Alice",
        }

        items = await adapter._build_mentions(ROOM)

        assert [i.id for i in items] == [HUMAN_ID]

    async def test_falls_back_to_participants_when_nobody_has_spoken(self, adapter):
        items = await adapter._build_mentions(ROOM)

        assert sorted(i.id for i in items) == sorted([PEER_ID, HUMAN_ID])

    async def test_is_per_room(self, adapter):
        other = "room-xyz"
        adapter._participants_cache[other] = adapter._participants_cache[ROOM]
        await adapter._handle_message_created(_event(PEER_ID, "Agent"))
        await adapter._handle_message_created(_event(HUMAN_ID, "User", room_id=other))

        assert [i.id for i in await adapter._build_mentions(ROOM)] == [PEER_ID]
        assert [i.id for i in await adapter._build_mentions(other)] == [HUMAN_ID]
