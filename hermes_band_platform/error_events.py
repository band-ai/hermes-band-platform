"""Band ``error`` events for failed turns.

When a turn fails, the room is the only place the user is looking. The host
does surface *some* failures as ordinary replies (``gateway.run``'s
``_normalize_empty_agent_response`` turns an agent failure into "The request
failed: …", and ``BasePlatformAdapter._process_message_background`` sends a
"Sorry, I encountered an error" message when the handler raises) — but every
one of those is a *message*, and a message needs at least one @mention to be
accepted by Band. When the send itself is what failed, the user is left with
silence that is indistinguishable from the agent still thinking, and the only
record is in the gateway log on the host.

A Band ``error`` **event** is exempt from the @mention requirement, so it is
the one thing that still lands when the message path is the broken part. This
module builds and posts it.

Design constraints, in the order they matter:

  * **Best-effort, never masking.** Emission is wrapped end-to-end; a failing
    event post is logged and swallowed. The original failure — and the
    adapter's own ``mark_failed`` ack — must survive it untouched.
  * **Fail-closed redaction.** A Band event cannot be deleted once written and
    the host does not scrub this path, so every payload goes through
    ``agent.redact.redact_sensitive_text(force=True)``. If that redactor is
    unavailable we DROP the event rather than emit unredacted text — an error
    string can easily carry a credential, a token in a URL, or a traceback
    full of environment values.
  * **The host's classification, not ours.** Where ``gateway.run`` already
    knows how to describe a provider failure (auth / policy / rate-limit), we
    reuse its wording so chat surfaces stay consistent.
  * **Never fed back to the agent.** Events are not text, and the plugin's own
    rehydration skips non-text context items (``adapter._seedable_text``), as
    does inbound dispatch (``adapter._handle_message_created``). So emitting
    cannot loop back into the agent's own transcript.

Sizing (``_EVENT_CONTENT_MAX_LENGTH``, head-and-tail truncation, blank-content
placeholder) mirrors the SDK's private ``band/runtime/tools.py`` helpers: the
platform rejects content over 16384 chars and blank content with a 422 before
it ever reaches the room.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple
from weakref import WeakKeyDictionary

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy SDK import guard — mirrors adapter.py. The gateway discovers plugins
# before dependencies are guaranteed present, so this module must import
# cleanly without band-sdk; emission then no-ops (the adapter's own preflight
# is what actually reports the missing dependency).
# ---------------------------------------------------------------------------
try:
    from band.client.rest import ChatEventRequest, DEFAULT_REQUEST_OPTIONS
    from band.core.types import MessageType as BandMessageType
except ImportError:  # pragma: no cover - exercised only without band-sdk
    ChatEventRequest = None
    DEFAULT_REQUEST_OPTIONS = {"max_retries": 3}
    BandMessageType = None


# Platform limits for event content (thenvoi-platform ``events_controller.ex``):
# over-long content and blank content are both rejected with a 422 before the
# event reaches the room. Copied from the SDK's private ``band/runtime/tools.py``
# rather than imported, so an older band-sdk without those helpers still works.
_EVENT_CONTENT_MAX_LENGTH = 16384
_EVENT_TRUNCATION_MARKER = "... [truncated] ..."
_EVENT_EMPTY_CONTENT_PLACEHOLDER = "(no content)"

# How much of a raw failure reason to show in the room. The event carries a
# glance-able explanation for a human, not a log line — the full text is in the
# gateway log.
_REASON_MAX_LENGTH = 300

_FAILURE_HEADLINE = "⚠️ I couldn't finish that turn."
_NO_DETAIL_LINE = "No details reached this room — check the gateway logs."

# Band's ``error`` event metadata shape is ``{error_code, details}``.
_ERROR_CODE_TURN_FAILED = "turn_failed"
_ERROR_CODE_DELIVERY_FAILED = "delivery_failed"

# Most recent failed send per adapter, as ``(room_id, error_text)``. Populated
# by ``note_send_failure`` from the adapter's send path and consumed once by
# ``report_turn_failure`` — a failed delivery is the single most common way a
# Band turn goes silent, and it is the only failure reason that exists
# in-process at the time ``on_processing_complete`` fires (the hook itself
# carries no reason, only an outcome).
#
# Weak keys so a discarded adapter is not pinned, and one entry per adapter so
# it cannot grow: a stale reason is either overwritten by the next failure or
# cleared by the next success.
_LAST_SEND_FAILURE: "WeakKeyDictionary[Any, Tuple[str, str]]" = WeakKeyDictionary()


def _short(value: Any) -> str:
    """Truncate an id to ``first8…`` for low-cardinality logs.

    Duplicated from ``adapter._short_id`` on purpose: ``adapter`` imports this
    module at its top level, so importing back would be circular.
    """
    if not value:
        return "<none>"
    text = str(value)
    if len(text) <= 8:
        return text
    return f"{text[:8]}…"


def _truncate_event_content(content: str) -> str:
    """Cap *content* at the platform limit, keeping its head and tail.

    Both ends are preserved because the tail of a truncated failure dump is
    often the informative part. A no-op when *content* already fits, so callers
    can run it unconditionally.
    """
    if len(content) <= _EVENT_CONTENT_MAX_LENGTH:
        return content
    budget = _EVENT_CONTENT_MAX_LENGTH - len(_EVENT_TRUNCATION_MARKER)
    head_len = budget // 2
    tail_len = budget - head_len
    return content[:head_len] + _EVENT_TRUNCATION_MARKER + content[-tail_len:]


def redact_event_text(text: Any) -> Optional[str]:
    """Redact *text* for a Band event, or ``None`` when it cannot be redacted.

    **Fail-closed.** A Band event is permanent — the agent surface has no
    delete, and ``supersede`` only de-lists — so an unredacted credential
    written here cannot be taken back. ``None`` therefore means "drop the
    event", never "emit as-is". ``force=True`` redacts even when
    ``security.redact_secrets`` is off, matching every other safety boundary in
    the host (``_redact_approval_command``, ``_redact_gateway_user_facing_secrets``).

    ``redact_url_credentials=True`` goes one step beyond what the host's own
    chat-egress redactor asks for, and deliberately: it is what masks
    ``https://user:pass@host`` userinfo and credential-named query parameters.
    The redactor leaves those alone by default so that actionable OAuth
    callback / magic-link / pre-signed URLs survive ordinary tool flows — but a
    permanent failure notice in a shared room is a *non-navigation egress
    boundary*, nobody is going to click a URL out of it, and a connection error
    is one of the likeliest places for a credentialed URL to show up.
    """
    try:
        from agent.redact import redact_sensitive_text
    except Exception:
        return None
    try:
        return redact_sensitive_text(
            str(text or ""), force=True, redact_url_credentials=True
        )
    except TypeError:
        # Older agent.redact without the URL-credential switch — still redact.
        try:
            return redact_sensitive_text(str(text or ""), force=True)
        except Exception:
            return None
    except Exception:
        return None


def describe_reason(text: str) -> Optional[str]:
    """Render an already-redacted failure reason as a human-readable line.

    Delegates to the host's own provider-error classifier so a quota / rate
    limit reads as one ("⏱️ The model provider is rate-limiting requests…")
    instead of as a raw envelope, and so chat wording stays identical to every
    other Hermes surface. The rate-limit regex is applied on its own as well:
    ``_looks_like_gateway_provider_error`` only fires when the marker leads the
    string, and a 429 usually arrives wrapped in other text.

    Falls back to the reason itself (trimmed) when the host classifier is
    unavailable or recognizes nothing — a raw-but-redacted reason in the room
    still beats silence.
    """
    reason = str(text or "").strip()
    if not reason:
        return None
    try:
        from gateway.run import (
            _GATEWAY_RATE_LIMIT_RE,
            _gateway_provider_error_reply,
            _looks_like_gateway_provider_error,
        )

        if _looks_like_gateway_provider_error(reason) or _GATEWAY_RATE_LIMIT_RE.search(
            reason
        ):
            return _gateway_provider_error_reply(reason)
    except Exception:
        pass
    if len(reason) > _REASON_MAX_LENGTH:
        reason = reason[: _REASON_MAX_LENGTH - 1] + "…"
    return reason


def note_send_failure(adapter: Any, room_id: str, error: Any) -> None:
    """Record (``error``) or clear (``error is None``) the last failed send.

    Called from the adapter's send path. The clear is what keeps attribution
    honest: without it, a send that failed and then recovered would still be
    reported as the reason for an unrelated turn failure minutes later.
    """
    try:
        if error is None:
            entry = _LAST_SEND_FAILURE.get(adapter)
            if entry is not None and entry[0] == str(room_id):
                _LAST_SEND_FAILURE.pop(adapter, None)
            return
        _LAST_SEND_FAILURE[adapter] = (str(room_id), str(error))
    except TypeError:
        # Non-weakref-able stand-in (test doubles); losing the reason only
        # costs detail in the event, never correctness.
        pass


def pop_send_failure(adapter: Any, room_id: str) -> Optional[str]:
    """Consume the recorded send failure for ``room_id``, if it is that room's."""
    try:
        entry = _LAST_SEND_FAILURE.get(adapter)
    except TypeError:
        return None
    if entry is None or entry[0] != str(room_id):
        return None
    _LAST_SEND_FAILURE.pop(adapter, None)
    return entry[1]


def _fatal_error_reason(adapter: Any) -> Optional[str]:
    """The adapter's standing fatal error, when one is set.

    A dead consumer loop or a failed connect leaves the link marked fatal while
    the REST client still works, so this is often the real story behind a turn
    that produced nothing.
    """
    try:
        if getattr(adapter, "has_fatal_error", False):
            return getattr(adapter, "fatal_error_message", None)
    except Exception:
        pass
    return None


def build_failure_event(
    raw_reason: Optional[str] = None,
    *,
    message_id: Optional[str] = None,
    error_code: str = _ERROR_CODE_TURN_FAILED,
) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    """Build ``(content, metadata)`` for a turn-failure event.

    Returns ``(None, None)`` when the payload cannot be redacted — the caller
    must then drop the event (see :func:`redact_event_text`).
    """
    reason: Optional[str] = None
    if raw_reason:
        safe = redact_event_text(raw_reason)
        if safe is None:
            return None, None
        # Classify only redacted text, so a secret can never reach the host's
        # regexes or be echoed back inside its reply.
        reason = describe_reason(safe)

    content = redact_event_text(f"{_FAILURE_HEADLINE}\n{reason or _NO_DETAIL_LINE}")
    if content is None:
        return None, None
    content = _truncate_event_content(content.strip()) or _EVENT_EMPTY_CONTENT_PLACEHOLDER

    metadata: Dict[str, Any] = {"error_code": error_code}
    if reason:
        metadata["details"] = reason
    if message_id:
        # Band's own id for the message whose turn failed, in this same room —
        # useful for correlation and not a secret the room doesn't already hold.
        metadata["message_id"] = str(message_id)
    return content, metadata


async def emit_error_event(
    adapter: Any,
    room_id: str,
    content: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    """Post one ``error`` event to a room. Never raises.

    Deliberately does NOT go through ``adapter.send``: events carry no mentions
    (Band rejects a mention-less *message* with a 422, but accepts a
    mention-less *event*), which is exactly why an event still lands when the
    message path is the thing that failed.
    """
    link = getattr(adapter, "_link", None)
    if link is None or ChatEventRequest is None or BandMessageType is None:
        return False
    try:
        await link.rest.agent_api_events.create_agent_chat_event(
            chat_id=room_id,
            event=ChatEventRequest(
                content=content,
                message_type=BandMessageType.ERROR,
                metadata=metadata,
            ),
            request_options=DEFAULT_REQUEST_OPTIONS,
        )
        return True
    except Exception as e:
        logger.warning(
            "[band] Could not emit error event to room %s: %s", _short(room_id), e
        )
        return False


async def report_turn_failure(adapter: Any, event: Any, outcome: Any) -> None:
    """Surface a failed turn in its room as a Band ``error`` event.

    Wired from ``BandAdapter.on_processing_complete``, which is the only
    adapter-facing seam the host drives on *every* turn failure — the handler
    raising, an unexpected cancellation, and (the silent one) a response that
    was produced but could not be delivered all funnel into
    ``ProcessingOutcome.FAILURE`` there.

    Only ``FAILURE`` emits. ``CANCELLED`` is a ``/stop`` or a superseded turn —
    its silence is intentional and announcing it would be noise.

    Never raises: a failure here must not mask the failure it is reporting, nor
    the caller's own server-side ack.
    """
    try:
        if str(getattr(outcome, "value", outcome)) != "failure":
            return
        room_id = getattr(getattr(event, "source", None), "chat_id", None)
        if not room_id:
            return

        send_error = pop_send_failure(adapter, room_id)
        content, metadata = build_failure_event(
            send_error or _fatal_error_reason(adapter),
            message_id=getattr(event, "message_id", None),
            error_code=(
                _ERROR_CODE_DELIVERY_FAILED if send_error else _ERROR_CODE_TURN_FAILED
            ),
        )
        if content is None:
            logger.warning(
                "[band] Dropping error event for room %s — redaction unavailable",
                _short(room_id),
            )
            return
        await emit_error_event(adapter, room_id, content, metadata)
    except Exception as e:
        logger.debug("[band] Error-event reporting failed: %s", e)
