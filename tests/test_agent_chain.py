"""Agent-to-agent chain bound.

The automatic reply @mentions peer agents (``_mention_items``' fallback branch),
so two agents alone in a room answer each other indefinitely. These tests pin the
budget that stops it, and — just as importantly — pin that it never charges a
conversation a human is part of.

Everything here drives ``_handle_message_created``, the real entry point, rather
than the budget helper alone: the property that matters is "the model did not run
a turn", and only the full path shows that.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from hermes_band_platform.adapter import (
    _AGENT_CHAIN_MAX_DEFAULT,
    _ERROR_CODE_AGENT_CHAIN_CAPPED,
    BandAdapter,
    _read_agent_chain_max,
)

SELF_ID = "agent-self-id"
PEER_ID = "agent-peer-id"
PEER2_ID = "agent-peer2-id"
HUMAN_ID = "human-sender"
ROOM = "room-abc"


def _make_adapter(monkeypatch, agent_id=SELF_ID):
    from gateway.config import PlatformConfig

    monkeypatch.setenv("BAND_AGENT_ID", agent_id)
    monkeypatch.setenv("BAND_API_KEY", "secret-key")
    monkeypatch.delenv("BAND_BASE_URL", raising=False)
    monkeypatch.delenv("BAND_OWNER_ID", raising=False)
    return BandAdapter(PlatformConfig(enabled=True, extra={}))


@pytest.fixture
def emitted(monkeypatch):
    """Capture error events instead of posting them; no link is required."""
    calls = []

    async def _capture(adapter, room_id, content, metadata=None):
        calls.append((room_id, content, metadata or {}))
        return True

    monkeypatch.setattr("hermes_band_platform.adapter.emit_error_event", _capture)
    return calls


@pytest.fixture
def adapter(monkeypatch):
    a = _make_adapter(monkeypatch)
    a._agent_id = SELF_ID
    a.handle_message = AsyncMock()
    a._ack_consumed = AsyncMock()
    a._participants_cache[ROOM] = [
        {"id": SELF_ID, "type": "Agent", "name": "Bot", "handle": "bot"},
        {"id": PEER_ID, "type": "Agent", "name": "Peer", "handle": "peer"},
        {"id": PEER2_ID, "type": "Agent", "name": "Peer2", "handle": "peer2"},
        {"id": HUMAN_ID, "type": "User", "name": "Alice", "handle": "alice"},
    ]
    return a


_counter = iter(range(1, 10_000))


def _event(sender_id, sender_type, room_id=ROOM):
    """An addressed text message — every Band room is mention-gated."""
    payload = SimpleNamespace(
        id=f"msg-{next(_counter):04d}",
        content="hello",
        message_type="text",
        sender_id=sender_id,
        sender_type=sender_type,
        sender_name="Sender",
        chat_room_id=room_id,
        metadata=SimpleNamespace(mentions=[SimpleNamespace(id=SELF_ID, handle=None)]),
    )
    return SimpleNamespace(type="message_created", room_id=room_id, payload=payload)


async def _peer_turns(adapter, count, sender_id=PEER_ID):
    for _ in range(count):
        await adapter._handle_message_created(_event(sender_id, "Agent"))


class TestChainBudget:
    async def test_allows_up_to_the_cap_then_stops_running_turns(self, adapter, emitted):
        adapter._agent_chain_max = 3

        await _peer_turns(adapter, 5)

        assert adapter.handle_message.await_count == 3

    async def test_capped_message_is_acked_so_next_does_not_re_offer_it(
        self, adapter, emitted
    ):
        adapter._agent_chain_max = 1

        await _peer_turns(adapter, 2)

        # One turn ran; the suppressed one was terminally acked rather than left
        # to be re-offered on the next drain.
        assert adapter.handle_message.await_count == 1
        assert adapter._ack_consumed.await_count == 1

    async def test_notice_is_emitted_exactly_once_per_trip(self, adapter, emitted):
        adapter._agent_chain_max = 1

        await _peer_turns(adapter, 4)

        assert len(emitted) == 1
        room_id, content, metadata = emitted[0]
        assert room_id == ROOM
        assert metadata["error_code"] == _ERROR_CODE_AGENT_CHAIN_CAPPED
        assert metadata["chain_max"] == 1
        # The notice has to say how to lift it, or a capped room looks broken.
        assert "human" in content.lower()
        assert "BAND_AGENT_CHAIN_MAX" in content

    async def test_a_three_agent_cycle_is_bounded(self, adapter, emitted):
        """Per-room, not per-pair: A→B→C→A never repeats a pair."""
        adapter._agent_chain_max = 2

        await adapter._handle_message_created(_event(PEER_ID, "Agent"))
        await adapter._handle_message_created(_event(PEER2_ID, "Agent"))
        await adapter._handle_message_created(_event(PEER_ID, "Agent"))

        assert adapter.handle_message.await_count == 2
        assert len(emitted) == 1

    async def test_the_bound_is_per_room(self, adapter, emitted):
        adapter._agent_chain_max = 1
        other = "room-xyz"
        adapter._participants_cache[other] = adapter._participants_cache[ROOM]

        await _peer_turns(adapter, 2)
        await adapter._handle_message_created(_event(PEER_ID, "Agent", room_id=other))

        # The second room still has its full budget.
        assert adapter.handle_message.await_count == 2


class TestHumanResets:
    async def test_a_human_message_refills_the_budget(self, adapter, emitted):
        adapter._agent_chain_max = 1

        await _peer_turns(adapter, 2)  # one turn, then capped
        await adapter._handle_message_created(_event(HUMAN_ID, "User"))
        await _peer_turns(adapter, 1)

        # peer, human, peer — the second peer turn ran because a person spoke.
        assert adapter.handle_message.await_count == 3
        assert ROOM not in adapter._agent_chain_tripped

    async def test_a_second_trip_emits_a_second_notice(self, adapter, emitted):
        """The trip flag suppresses repeats within one trip, not across trips."""
        adapter._agent_chain_max = 1

        await _peer_turns(adapter, 2)
        await adapter._handle_message_created(_event(HUMAN_ID, "User"))
        await _peer_turns(adapter, 2)

        assert len(emitted) == 2

    async def test_human_turns_never_charge_the_budget(self, adapter, emitted):
        adapter._agent_chain_max = 1

        for _ in range(5):
            await adapter._handle_message_created(_event(HUMAN_ID, "User"))

        assert adapter.handle_message.await_count == 5
        assert adapter._agent_chain.get(ROOM, 0) == 0
        assert emitted == []


class TestConfiguredExtremes:
    async def test_zero_refuses_peer_agent_turns_outright(self, adapter, emitted):
        adapter._agent_chain_max = 0

        await _peer_turns(adapter, 3)

        assert adapter.handle_message.await_count == 0
        assert len(emitted) == 1

    async def test_zero_still_lets_humans_through(self, adapter, emitted):
        adapter._agent_chain_max = 0

        await adapter._handle_message_created(_event(HUMAN_ID, "User"))

        assert adapter.handle_message.await_count == 1

    async def test_negative_is_explicitly_unbounded(self, adapter, emitted):
        adapter._agent_chain_max = -1

        await _peer_turns(adapter, 25)

        assert adapter.handle_message.await_count == 25
        assert emitted == []
        # Unbounded must not accumulate per-room state either.
        assert adapter._agent_chain == {}


class TestChainStateLifecycle:
    async def test_disconnect_clears_the_chain(self, adapter, emitted):
        adapter._agent_chain_max = 1
        await _peer_turns(adapter, 2)
        assert adapter._agent_chain and adapter._agent_chain_tripped

        await adapter.disconnect()

        # A reconnect re-offers backlog; carrying a count across the gap would
        # cap a room for an exchange that belonged to the previous link.
        assert adapter._agent_chain == {}
        assert adapter._agent_chain_tripped == set()

    async def test_chain_state_starts_empty(self, adapter):
        assert adapter._agent_chain == {}
        assert adapter._agent_chain_tripped == set()


class TestConfigParsing:
    def test_unset_uses_the_default(self, monkeypatch):
        monkeypatch.delenv("BAND_AGENT_CHAIN_MAX", raising=False)
        assert _read_agent_chain_max() == _AGENT_CHAIN_MAX_DEFAULT

    @pytest.mark.parametrize("raw,expected", [("3", 3), ("0", 0), ("-1", -1), (" 12 ", 12)])
    def test_valid_values(self, monkeypatch, raw, expected):
        monkeypatch.setenv("BAND_AGENT_CHAIN_MAX", raw)
        assert _read_agent_chain_max() == expected

    def test_unparseable_falls_back_to_the_default_not_to_an_extreme(
        self, monkeypatch, caplog
    ):
        monkeypatch.setenv("BAND_AGENT_CHAIN_MAX", "lots")
        with caplog.at_level("WARNING"):
            assert _read_agent_chain_max() == _AGENT_CHAIN_MAX_DEFAULT
        assert "BAND_AGENT_CHAIN_MAX" in caplog.text
