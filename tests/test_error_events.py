"""Tests for Band ``error`` events on failed turns.

The band SDK stub is installed by ``tests/conftest.py`` at collection time,
BEFORE this module imports the adapter — so ``error_events`` binds the stub's
``ChatEventRequest`` / ``MessageType`` and the emission path is exercised
against request objects with the real keyword signatures, not auto-attr
MagicMocks.
"""

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import hermes_band_platform.adapter as _band_mod
import hermes_band_platform.error_events as _events_mod

BandAdapter = _band_mod.BandAdapter
ProcessingOutcome = _band_mod.ProcessingOutcome


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_adapter(monkeypatch, agent_id="agent-uuid-1234", api_key="secret-key"):
    from gateway.config import PlatformConfig

    monkeypatch.setenv("BAND_AGENT_ID", agent_id)
    monkeypatch.setenv("BAND_API_KEY", api_key)
    for key in ("BAND_BASE_URL", "BAND_OWNER_ID", "BAND_HUB_ROOM", "BAND_HOME_ROOM"):
        monkeypatch.delenv(key, raising=False)
    return BandAdapter(PlatformConfig(enabled=True, extra={}))


def _event_link():
    """A MagicMock link with the ack + event helpers wired as AsyncMocks."""
    link = MagicMock()
    link.mark_processing = AsyncMock()
    link.mark_processed = AsyncMock()
    link.mark_failed = AsyncMock()
    link.rest.agent_api_events.create_agent_chat_event = AsyncMock()
    return link


def _huge_traceback(frames=3000):
    """A large, realistically shaped failure dump (~150KB).

    Shape matters, not just size: the host redactor's cost explodes on long
    unbroken runs of ``[A-Za-z0-9+.-]``, and a real traceback is broken up by
    spaces and newlines. Using one keeps these tests honest AND fast.
    """
    return "Traceback (most recent call last):\n" + "\n".join(
        f'  File "/srv/app/module{i}.py", line {i}, in handler' for i in range(frames)
    )


def _evt(msg_id="m1", room_id="room-abc", internal=False):
    return SimpleNamespace(
        message_id=msg_id,
        internal=internal,
        source=SimpleNamespace(chat_id=room_id),
    )


def _posted(adapter):
    """The single ``ChatEventRequest`` posted through the link, or None."""
    call = adapter._link.rest.agent_api_events.create_agent_chat_event
    if not call.await_args_list:
        return None
    return call.await_args.kwargs["event"]


@pytest.fixture
def adapter(monkeypatch):
    a = _make_adapter(monkeypatch)
    a._agent_id = "agent-uuid-1234"
    a._link = _event_link()
    return a


# ---------------------------------------------------------------------------
# 1. When an event is emitted
# ---------------------------------------------------------------------------

class TestFailureEmission:

    @pytest.mark.asyncio
    async def test_failure_emits_error_event(self, adapter):
        await adapter.on_processing_complete(_evt(), ProcessingOutcome.FAILURE)
        event = _posted(adapter)
        assert event is not None
        assert event.message_type == "error"
        call = adapter._link.rest.agent_api_events.create_agent_chat_event
        assert call.await_args.kwargs["chat_id"] == "room-abc"

    @pytest.mark.asyncio
    async def test_event_carries_no_mentions(self, adapter):
        # Events are exempt from Band's @mention requirement, and attaching
        # mentions to one is what the sibling slices agreed never to do.
        await adapter.on_processing_complete(_evt(), ProcessingOutcome.FAILURE)
        assert not hasattr(_posted(adapter), "mentions")

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "outcome", [ProcessingOutcome.SUCCESS, ProcessingOutcome.CANCELLED]
    )
    async def test_non_failure_outcomes_emit_nothing(self, adapter, outcome):
        # A cancelled turn was /stop-ped or superseded — its silence is intended.
        await adapter.on_processing_complete(_evt(), outcome)
        assert _posted(adapter) is None

    @pytest.mark.asyncio
    async def test_internal_event_emits_nothing(self, adapter):
        # Synthetic participant notices are not the user's turn.
        await adapter.on_processing_complete(
            _evt(internal=True), ProcessingOutcome.FAILURE
        )
        assert _posted(adapter) is None

    @pytest.mark.asyncio
    async def test_emits_even_without_a_message_id(self, adapter):
        # The ack path gives up without an id; the user still deserves to know.
        await adapter.on_processing_complete(
            _evt(msg_id=None), ProcessingOutcome.FAILURE
        )
        assert _posted(adapter) is not None
        adapter._link.mark_failed.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_room_id_emits_nothing(self, adapter):
        event = SimpleNamespace(message_id="m1", internal=False, source=None)
        await adapter.on_processing_complete(event, ProcessingOutcome.FAILURE)
        assert _posted(adapter) is None

    @pytest.mark.asyncio
    async def test_no_link_emits_nothing_and_does_not_raise(self, adapter):
        adapter._link = None
        await adapter.on_processing_complete(_evt(), ProcessingOutcome.FAILURE)


# ---------------------------------------------------------------------------
# 2. Best-effort — reporting must never mask the failure it reports
# ---------------------------------------------------------------------------

class TestBestEffort:

    @pytest.mark.asyncio
    async def test_emission_failure_does_not_break_the_ack(self, adapter):
        adapter._link.rest.agent_api_events.create_agent_chat_event.side_effect = (
            RuntimeError("event post exploded")
        )
        await adapter.on_processing_complete(_evt(), ProcessingOutcome.FAILURE)
        # The server-side settle still happened.
        adapter._link.mark_failed.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_report_swallows_anything(self, adapter, monkeypatch):
        monkeypatch.setattr(
            _events_mod,
            "build_failure_event",
            MagicMock(side_effect=RuntimeError("boom")),
        )
        await _events_mod.report_turn_failure(
            adapter, _evt(), ProcessingOutcome.FAILURE
        )

    @pytest.mark.asyncio
    async def test_emit_returns_false_on_post_failure(self, adapter):
        adapter._link.rest.agent_api_events.create_agent_chat_event.side_effect = (
            RuntimeError("nope")
        )
        assert await _events_mod.emit_error_event(adapter, "room-abc", "x") is False


# ---------------------------------------------------------------------------
# 3. Redaction is fail-closed
# ---------------------------------------------------------------------------

class TestRedaction:

    @pytest.mark.asyncio
    async def test_event_is_dropped_when_the_redactor_is_unavailable(
        self, adapter, monkeypatch
    ):
        # A Band event cannot be deleted once written, so an unavailable
        # redactor must mean "drop", never "emit raw".
        monkeypatch.setitem(sys.modules, "agent.redact", None)
        await adapter.on_processing_complete(_evt(), ProcessingOutcome.FAILURE)
        assert _posted(adapter) is None

    def test_redact_event_text_returns_none_when_unavailable(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "agent.redact", None)
        assert _events_mod.redact_event_text("anything") is None

    def test_build_failure_event_drops_when_unavailable(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "agent.redact", None)
        assert _events_mod.build_failure_event("boom") == (None, None)

    @pytest.mark.asyncio
    async def test_secret_in_the_reason_is_redacted(self, adapter):
        secret = "sk-" + "A1b2C3d4E5f6G7h8"
        _events_mod.note_send_failure(
            adapter, "room-abc", f"POST rejected with Authorization: Bearer {secret}"
        )
        await adapter.on_processing_complete(_evt(), ProcessingOutcome.FAILURE)
        event = _posted(adapter)
        assert secret not in event.content
        assert secret not in str(event.metadata)

    @pytest.mark.asyncio
    async def test_url_credentials_are_redacted(self, adapter):
        # A permanent notice in a shared room is a non-navigation egress
        # boundary, so userinfo in a URL must not survive it.
        _events_mod.note_send_failure(
            adapter, "room-abc", "Connection refused to https://svc:hunter2@app.band.ai/api"
        )
        await adapter.on_processing_complete(_evt(), ProcessingOutcome.FAILURE)
        assert "hunter2" not in _posted(adapter).content


# ---------------------------------------------------------------------------
# 4. The reason: recorded by the send path, described by the host
# ---------------------------------------------------------------------------

class TestReason:

    @pytest.mark.asyncio
    async def test_send_failure_reason_reaches_the_room(self, adapter):
        _events_mod.note_send_failure(
            adapter, "room-abc", "No mentionable recipient (Band requires >=1 mention)"
        )
        await adapter.on_processing_complete(_evt(), ProcessingOutcome.FAILURE)
        event = _posted(adapter)
        assert "No mentionable recipient" in event.content
        assert event.metadata["error_code"] == "delivery_failed"
        assert event.metadata["message_id"] == "m1"

    @pytest.mark.asyncio
    async def test_reason_is_consumed_once(self, adapter):
        _events_mod.note_send_failure(adapter, "room-abc", "one-shot reason")
        await adapter.on_processing_complete(_evt(), ProcessingOutcome.FAILURE)
        adapter._link.rest.agent_api_events.create_agent_chat_event.reset_mock()
        await adapter.on_processing_complete(_evt(), ProcessingOutcome.FAILURE)
        event = _posted(adapter)
        assert "one-shot reason" not in event.content
        assert event.metadata["error_code"] == "turn_failed"

    @pytest.mark.asyncio
    async def test_reason_is_not_used_for_another_room(self, adapter):
        _events_mod.note_send_failure(adapter, "room-other", "other room's problem")
        await adapter.on_processing_complete(_evt(), ProcessingOutcome.FAILURE)
        assert "other room's problem" not in _posted(adapter).content

    def test_successful_send_clears_the_reason(self, adapter):
        _events_mod.note_send_failure(adapter, "room-abc", "transient blip")
        _events_mod.note_send_failure(adapter, "room-abc", None)
        assert _events_mod.pop_send_failure(adapter, "room-abc") is None

    @pytest.mark.asyncio
    async def test_send_path_records_the_missing_mention_failure(self, adapter):
        # The Band-specific silent failure: no mentionable recipient means the
        # reply is dropped before it is ever posted.
        adapter._build_mentions = AsyncMock(return_value=[])
        result = await adapter.send("room-abc", "a reply nobody will see")
        assert result.success is False
        assert (
            _events_mod.pop_send_failure(adapter, "room-abc")
            == "No mentionable recipient (Band requires >=1 mention)"
        )

    @pytest.mark.asyncio
    async def test_send_path_records_api_errors(self, adapter):
        adapter._build_mentions = AsyncMock(return_value=[MagicMock()])
        adapter._link.rest.agent_api_messages.create_agent_chat_message = AsyncMock(
            side_effect=RuntimeError("422 Unprocessable Entity")
        )
        await adapter.send("room-abc", "hello")
        assert "422" in (_events_mod.pop_send_failure(adapter, "room-abc") or "")

    @pytest.mark.asyncio
    async def test_send_path_clears_the_reason_on_success(self, adapter):
        adapter._build_mentions = AsyncMock(return_value=[MagicMock()])
        adapter._link.rest.agent_api_messages.create_agent_chat_message = AsyncMock(
            return_value=SimpleNamespace(data=SimpleNamespace(id="sent-1"))
        )
        _events_mod.note_send_failure(adapter, "room-abc", "stale blip")
        await adapter.send("room-abc", "hello")
        assert _events_mod.pop_send_failure(adapter, "room-abc") is None

    def test_rate_limit_reason_reads_as_a_quota_failure(self):
        # The host's own classifier owns this wording — a 429 must not surface
        # as a raw envelope.
        line = _events_mod.describe_reason("Rate limited after 5 retries: HTTP 429")
        assert "rate-limiting" in line.lower()
        assert "429" not in line

    def test_usage_limit_reason_reads_as_a_quota_failure(self):
        line = _events_mod.describe_reason(
            "API call failed: usage limit reached for this plan"
        )
        assert "rate-limiting" in line.lower()

    def test_unclassifiable_reason_survives_verbatim(self):
        assert _events_mod.describe_reason("socket closed") == "socket closed"

    def test_blank_reason_is_none(self):
        assert _events_mod.describe_reason("   ") is None

    def test_long_reason_is_capped_for_readability(self):
        line = _events_mod.describe_reason("z" * 5000)
        assert len(line) == _events_mod._REASON_MAX_LENGTH
        assert _events_mod._REASON_TRUNCATION_MARKER in line

    def test_long_reason_keeps_its_tail(self):
        # The last line of a traceback is usually the one that says what broke.
        line = _events_mod.describe_reason("z" * 5000 + "ConnectionResetError")
        assert line.endswith("ConnectionResetError")

    @pytest.mark.asyncio
    async def test_fatal_adapter_error_is_used_when_no_send_failed(self, adapter):
        adapter._set_fatal_error("consumer_died", "link queue closed", retryable=True)
        await adapter.on_processing_complete(_evt(), ProcessingOutcome.FAILURE)
        assert "link queue closed" in _posted(adapter).content

    @pytest.mark.asyncio
    async def test_generic_failure_points_at_the_logs(self, adapter):
        await adapter.on_processing_complete(_evt(), ProcessingOutcome.FAILURE)
        event = _posted(adapter)
        assert "gateway logs" in event.content
        assert event.metadata["error_code"] == "turn_failed"
        assert "details" not in event.metadata


# ---------------------------------------------------------------------------
# 5. Platform content limits (422 guards)
# ---------------------------------------------------------------------------

class TestContentLimits:

    def test_short_content_is_untouched(self):
        assert _events_mod._truncate_event_content("hi") == "hi"

    def test_oversized_content_keeps_head_and_tail(self):
        body = ("H" * 20000) + "TAIL"
        out = _events_mod._truncate_event_content(body)
        assert len(out) == _events_mod._EVENT_CONTENT_MAX_LENGTH
        assert out.startswith("H")
        assert out.endswith("TAIL")
        assert _events_mod._EVENT_TRUNCATION_MARKER in out

    @pytest.mark.asyncio
    async def test_emitted_content_never_exceeds_the_platform_limit(self, adapter):
        _events_mod.note_send_failure(adapter, "room-abc", _huge_traceback())
        await adapter.on_processing_complete(_evt(), ProcessingOutcome.FAILURE)
        content = _posted(adapter).content
        assert 0 < len(content) <= _events_mod._EVENT_CONTENT_MAX_LENGTH

    def test_content_is_never_blank(self):
        content, _ = _events_mod.build_failure_event()
        assert content.strip()


# ---------------------------------------------------------------------------
# 6. Bounding the redactor's input
#
# agent.redact is quadratic in input length with redact_url_credentials=True
# (~0.2s at 16KB, ~8.5s at 100KB — the backtracking optional scheme prefix in
# _STRICT_URL_USERINFO_RE). We must never hand it an unbounded reason: this is
# the turn-failure path. Bounding must not weaken the guarantee, so both halves
# are pinned — the input is capped, AND a huge payload is still fully scrubbed.
# ---------------------------------------------------------------------------

class TestRedactionInputBounding:

    def test_redactor_never_sees_an_unbounded_reason(self, monkeypatch):
        seen = []

        def spy(text):
            # Deliberately does NOT call through: this asserts what the redactor
            # is *handed*, and calling the real one on the worst-case input is
            # precisely the cost being guarded against.
            seen.append(len(str(text)))
            return "redacted"

        monkeypatch.setattr(_events_mod, "redact_event_text", spy)
        _events_mod.build_failure_event("x" * 1_000_000)
        ceiling = (
            _events_mod._REASON_MAX_LENGTH
            + 2 * _events_mod._REDACTION_INPUT_MARGIN
            + len(_events_mod._REASON_TRUNCATION_MARKER)
        )
        assert seen and max(seen) <= ceiling

    def test_bounding_is_flat_in_the_size_of_the_reason(self):
        # 10x the input, same work — the property that turns 8.5s into 0.2s.
        assert len(_events_mod._bound_for_redaction("x" * 100_000)) == len(
            _events_mod._bound_for_redaction("x" * 1_000_000)
        )

    def test_short_reasons_are_not_bounded_at_all(self):
        assert _events_mod._bound_for_redaction("socket closed") == "socket closed"

    def test_margin_survives_the_final_cut(self):
        # The margin is what makes bounding safe: a credential straddling the
        # final _REASON_MAX_LENGTH cut must still have been whole when the
        # redactor saw it. Assert the retained head really does extend at least
        # _REDACTION_INPUT_MARGIN chars past what gets emitted.
        bounded = _events_mod._bound_for_redaction("x" * 1_000_000)
        head_kept = bounded.index(_events_mod._REASON_TRUNCATION_MARKER)
        head_emitted = len(
            _events_mod.describe_reason("y" * 1000).split(
                _events_mod._REASON_TRUNCATION_MARKER
            )[0]
        )
        assert head_kept - head_emitted >= _events_mod._REDACTION_INPUT_MARGIN

    def test_huge_payload_is_still_fully_redacted_at_both_ends(self):
        # Bounding keeps head and tail, so a secret at either end must still be
        # scrubbed — the middle is discarded and never reaches Band.
        head_secret = "ghp_" + "A" * 36
        tail_secret = "xoxb-" + "9" * 24
        content, metadata = _events_mod.build_failure_event(
            f"{head_secret}\n{_huge_traceback()}\n{tail_secret}"
        )
        assert head_secret not in content
        assert tail_secret not in content
        assert head_secret not in str(metadata)
        assert tail_secret not in str(metadata)

    def test_secret_straddling_the_emitted_cut_is_redacted(self):
        # The exact case the margin exists for: a credential that begins inside
        # the emitted window and runs past its end.
        secret = "ghp_" + "Z" * 400
        content, _ = _events_mod.build_failure_event(
            f"boom {secret}\n{_huge_traceback()}"
        )
        assert secret not in content
        assert "Z" * 100 not in content


# ---------------------------------------------------------------------------
# 6. Emitted events can never be fed back to the agent
# ---------------------------------------------------------------------------

class TestNoFeedback:

    def test_rehydration_skips_error_items(self):
        # adapter._seedable_text is the single gate for "which context items
        # carry replayable text"; an error event must not be one of them.
        item = SimpleNamespace(
            message_type="error",
            content="⚠️ I couldn't finish that turn.",
            sender_type="Agent",
            sender_id="agent-uuid-1234",
            sender_name="hermes",
        )
        assert _band_mod._seedable_text(item, []) is None

    @pytest.mark.asyncio
    async def test_inbound_dispatch_drops_error_messages(self, adapter):
        adapter._message_handler = AsyncMock()
        event = SimpleNamespace(
            type="message_created",
            room_id="room-abc",
            payload=SimpleNamespace(
                id="evt-1",
                chat_room_id="room-abc",
                content="⚠️ I couldn't finish that turn.",
                message_type="error",
                sender_id="someone-else",
                sender_type="User",
                sender_name="Someone",
            ),
        )
        assert await adapter._handle_message_created(event) is False
        adapter._message_handler.assert_not_called()
