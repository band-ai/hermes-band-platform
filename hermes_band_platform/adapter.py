"""
Band Platform Adapter for Hermes Agent.

A plugin-based gateway adapter that connects a Band agent to the Band
platform and relays text messages between Band chat rooms and the Hermes
agent.  It wraps the official ``band-sdk`` :class:`BandLink` (persistent
WebSocket + REST client) — it does NOT reimplement the Band protocol.

Configuration is env-driven (seeded into ``PlatformConfig.extra`` by
``_env_enablement`` during gateway config load):

    BAND_AGENT_ID    Band agent ID (UUID)              [required]
    BAND_API_KEY     Band agent API key                [required]
    BAND_BASE_URL    Band host base URL (default app.band.ai)
    ... see plugin.yaml for the full optional set.

Memory preload/write-through and cron standalone delivery are deferred to
later passes; their extension points are marked with ``# TODO (<pass>):``
below so they drop in cleanly.

Scope notes:
  * Band rooms are not threads — ``thread_id`` is always None.
  * Sends require at least one @mention (API enforces ≥1); mentions are built
    from the cached last-human-sender, falling back to all non-agent room
    participants.
  * The HUB: on connect the adapter ensures a private owner↔agent control
    room — the pinned ``BAND_HUB_ROOM`` if set, else a freshly created
    "Hermes Hub" — and wires it as the platform home channel (the Band main
    channel). Existing rooms are never adopted as the hub.
  * Slash commands are OWNER-ONLY, in any Band room — command-shaped
    messages from anyone else are dropped (one-time notice for humans,
    silent for agents; fail-closed when the owner is unresolved).
"""

import asyncio
import logging
import os
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

from gateway.config import HomeChannel, Platform, PlatformConfig  # noqa: E402
from gateway.platforms.base import (  # noqa: E402
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
    ProcessingOutcome,
    SendResult,
)
from gateway.session import SessionSource, build_session_key  # noqa: E402

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy SDK import guard.
#
# Mirror the slack/discord module-top pattern: try to import the Band SDK
# symbols we drive directly.  When the SDK isn't installed the module must
# still import cleanly (the gateway discovers plugins before deps are
# guaranteed present), so we fall back to ``BAND_AVAILABLE = False`` and bind
# the names to ``None``.  ``check_band_requirements()`` performs the real
# (re)binding on demand.
#
# NAME COLLISION: the SDK exports its own ``MessageEvent`` (a platform event
# with ``type == "message_created"``) which is DISTINCT from Hermes's
# ``gateway.platforms.base.MessageEvent``.  We import the SDK one under the
# alias ``BandMessageEvent`` so the Hermes ``MessageEvent`` above is never
# shadowed.
# ---------------------------------------------------------------------------
try:
    from band.platform.link import BandLink
    from band.platform.event import (
        MessageEvent as BandMessageEvent,
        RoomAddedEvent,
        RoomRemovedEvent,
        RoomDeletedEvent,
    )
    from band.client.rest import (
        ChatMessageRequest,
        ChatMessageRequestMentionsItem,
        ChatRoomRequest,
        ParticipantRequest,
        DEFAULT_REQUEST_OPTIONS,
    )

    BAND_AVAILABLE = True
except ImportError:
    BAND_AVAILABLE = False
    BandLink = None
    BandMessageEvent = None
    RoomAddedEvent = None
    RoomRemovedEvent = None
    RoomDeletedEvent = None
    ChatMessageRequest = None
    ChatMessageRequestMentionsItem = None
    ChatRoomRequest = None
    ParticipantRequest = None
    DEFAULT_REQUEST_OPTIONS = {"max_retries": 3}


# Default Band host — matches the SDK's own BandLink defaults
# (wss://app.band.ai/api/v1/socket/websocket, https://app.band.ai).
_DEFAULT_BAND_HOST = "app.band.ai"

# Backstop cap for the sent-message-id dedup set; evict half when exceeded.
_SENT_IDS_MAX = 5000

# Cap for per-room caches (participants, last-human-sender). They are only
# evicted on room_removed, so a long-lived agent in many rooms would otherwise
# grow them without bound; trim to half capacity when exceeded (re-fetched on
# demand, so eviction is a cache miss, not a failure).
_ROOM_CACHE_MAX = 2000

# Backstop: how many consecutive id-less messages a single room drain tolerates
# before giving up. An id-less message can't be claimed/acked, so the cursor
# can't advance past it; we skip it to keep draining the rest, but cap the skips
# so a server that pathologically re-offers an un-ackable message can't spin.
_MAX_DRAIN_IDLESS_SKIPS = 50

# Session/source chat_type for every Band room. Band has no DMs — every room is
# a group room regardless of participant count, mention-gated for all
# participants — and ``group_sessions_per_user`` is locked False, so a single
# shared session per room is the whole model. Pinning chat_type to one constant keeps
# the session key (``agent:main:band:group:{room_id}``) anchored solely on the
# stable room id — a room that gains/loses a participant can never silently
# re-key the conversation. Do NOT derive this from the live participant count.
_SESSION_CHAT_TYPE = "group"

# Owner-facing name of the hub. Band derives a room's title from its first
# message, so a freshly created hub's titling greeting leads with this line
# (see _build_hub_greeting). The greeting body is built at runtime so it can
# name the owner and the agent.
_HUB_TITLE = "Hermes Agent Hub"

# One-time notice posted when a slash command arrives from a non-owner human.
_OWNER_COMMAND_NOTICE = (
    "Slash commands are only accepted from this agent's owner."
)

# Default number of consecutive failed hub sends before the adapter fails over
# to a fresh hub room, and the per-connect backstop cap on how many failovers
# may happen (so a platform-wide outage can't spin up rooms without bound).
_HUB_FAILOVER_THRESHOLD_DEFAULT = 3
_HUB_FAILOVER_MAX_PER_CONNECT_DEFAULT = 5


def _int_env(name: str, default: int) -> int:
    """Read a positive int from env, falling back to ``default``.

    Non-numeric or non-positive values fall back so a fat-fingered override
    can never disable the failover safety net.
    """
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _short_id(value: Any) -> str:
    """Truncate a UUID / long id to ``first8…`` for safe, low-cardinality logs.

    Hermes has no core redaction hook for plugin log sites, so the adapter
    redacts room / agent / sender ids locally at every ``logger.*`` call.
    API keys are never logged at all (masked fully — never passed here).
    """
    if not value:
        return "<none>"
    text = str(value)
    if len(text) <= 8:
        return text
    return f"{text[:8]}…"


def _derive_urls(base_url: str) -> tuple[str, str]:
    """Derive (ws_url, rest_url) from a Band base URL.

    From ``BAND_BASE_URL`` (or the default host) build:
      ws_url   = wss://<host>/api/v1/socket/websocket
      rest_url = https://<host>
    """
    host = _DEFAULT_BAND_HOST
    raw = (base_url or "").strip()
    if raw:
        if "://" not in raw:
            raw = f"https://{raw}"
        parsed = urlsplit(raw)
        if parsed.hostname:
            host = parsed.hostname
            if parsed.port:
                host = f"{host}:{parsed.port}"
    ws_url = f"wss://{host}/api/v1/socket/websocket"
    rest_url = f"https://{host}"
    return ws_url, rest_url


class BandAdapter(BasePlatformAdapter):
    """Async Band adapter implementing the BasePlatformAdapter interface.

    Instantiated by the ``adapter_factory`` passed to ``register_platform()``.
    Drives :class:`BandLink` directly (NOT ``Agent.run()``): a persistent
    WebSocket link surfaces inbound events through ``async for event in link``,
    and outbound messages post via the REST client.
    """

    # No confirmed Band per-message content limit exists in the SDK / REST
    # types (``ChatMessageRequest.content`` carries no ``max_length``).  Use a
    # conservative safe default; revisit once Band documents a hard cap.
    MAX_MESSAGE_LENGTH = 4000

    def __init__(self, config: PlatformConfig, **kwargs):
        super().__init__(config, Platform("band"))

        extra = getattr(config, "extra", {}) or {}

        # Credentials: extra (seeded by _env_enablement) with os.getenv fallback.
        # Strip whitespace to match _env_enablement (avoids cfg/enablement drift).
        self._cfg_agent_id = (os.getenv("BAND_AGENT_ID") or extra.get("agent_id", "")).strip()
        self._api_key = (os.getenv("BAND_API_KEY") or extra.get("api_key", "")).strip()
        self._base_url = (os.getenv("BAND_BASE_URL") or extra.get("base_url", "")).strip()

        # Owner UUID override — normally resolved from the agent identity on
        # connect. Anchors the hub (owner control room) and the command gate.
        self._owner_uuid = os.getenv("BAND_OWNER_ID") or extra.get("owner_id", "") or None

        # Hub (owner control room) — pinned id from env/extra, else resolved or
        # created by _ensure_hub() on connect. The hub is the platform home
        # channel (cron / notification deliveries land there by default).
        self._hub_room_id: Optional[str] = (
            str(os.getenv("BAND_HUB_ROOM") or extra.get("hub_room", "") or "").strip()
            or None
        )
        # Rooms already given the one-time "commands are owner-only" notice.
        self._cmd_notice_rooms: set = set()

        # Runtime state
        self._link: Optional[Any] = None
        self._consumer_task: Optional[asyncio.Task] = None
        # Background /next catch-up drain (Route A). Started on connect and on
        # every link reconnect to pull messages missed while the agent was
        # offline; cancelled on disconnect.
        self._catch_up_task: Optional[asyncio.Task] = None
        # The agent's own UUID + handle, resolved from get_agent_me on connect.
        # ``_cfg_agent_id`` is the configured id used to open the link; the
        # resolved ``_agent_id`` is authoritative for self-filtering.
        self._agent_id: str = self._cfg_agent_id
        self._handle: str = ""

        # Room membership is two disjoint sets — a room is in at most one:
        #   _known_rooms : rooms the agent currently belongs to (subscribed; the
        #                  only set the catch-up drain iterates).
        #   _left_rooms  : rooms it has since left (room_removed/room_deleted),
        #                  dropped from _known_rooms so a long-lived agent
        #                  cycling through many rooms can't grow it without
        #                  bound, and kept here only as capped re-join memory.
        # Invariant: a ``room_added`` for a room in *either* set is a *re-join*
        # (its local session was reset on the prior leave), so it is flagged for
        # context rehydration; a room in neither is brand new.
        self._known_rooms: set = set()
        self._left_rooms: set = set()
        # Re-joined rooms whose next inbound message should pull recent Band
        # context into MessageEvent.channel_context (consumed once, then cleared).
        self._rehydrate_rooms: set = set()

        # Per-room participant cache: room_id -> list[{id, name, handle, type}].
        self._participants_cache: Dict[str, List[Dict[str, Any]]] = {}
        # Per-room last human sender: room_id -> {"id", "handle", "name"}.
        # Used to build mandatory mentions when replying.
        self._last_human_sender: Dict[str, Dict[str, Any]] = {}

        # Sent-message id backstop: ids we posted, so the consumer can drop the
        # platform's echo even if SDK-side self-filtering misses it.
        self._sent_ids: set = set()

        # Inbound-message ids already dispatched THIS process-lifetime. Guards
        # the narrow live-vs-catch-up race (a message both live-delivered and in
        # the /next backlog at reconnect) and any server re-offer from
        # double-processing. Intentionally NOT persisted: after a restart the
        # server cursor is authoritative and a re-offer SHOULD re-process.
        self._seen_inbound_ids: set = set()

        # Hub failover: when the hub room repeatedly rejects sends (e.g. it hit
        # its Band message limit, or it's persistently erroring) the adapter
        # creates a fresh owner room and re-wires it as the main channel.
        # ``_hub_send_failures`` counts *consecutive* failed hub sends and is
        # reset by any successful hub send; reaching the threshold triggers a
        # failover. ``_failover_in_progress`` guards the async failover against
        # reentrancy, and ``_hub_failovers_done`` caps failovers per connect.
        self._hub_failover_threshold = _int_env(
            "BAND_HUB_FAILOVER_THRESHOLD", _HUB_FAILOVER_THRESHOLD_DEFAULT
        )
        self._hub_failover_max_per_connect = _int_env(
            "BAND_HUB_FAILOVER_MAX_PER_CONNECT", _HUB_FAILOVER_MAX_PER_CONNECT_DEFAULT
        )
        self._hub_send_failures: int = 0
        self._failover_in_progress: bool = False
        self._hub_failovers_done: int = 0

        # Scoped-lock identity (best-effort; set in connect()).
        self._lock_identity: Optional[str] = None

    @property
    def name(self) -> str:
        return "Band"

    @property
    def enforces_own_access_policy(self) -> bool:
        """Band's platform ACL is the access gate — trust it (no Hermes allowlist).

        A message only reaches this adapter if Band delivered it: the agent
        receives ``message_created`` events for rooms it participates in, and a
        user can only message the agent or add it to a room when Band's own access
        control permits it. So an inbound message arriving at the gateway has,
        by definition, already passed Band's ACL — exactly the intake-gating
        contract this flag denotes (see ``BasePlatformAdapter`` and the WeCom /
        Weixin / Yuanbao / QQBot adapters).

        Returning ``True`` makes the gateway treat Band traffic as
        already-authorized and skip its env-allowlist default-deny, so a fresh
        install (just ``BAND_AGENT_ID`` + ``BAND_API_KEY``) is reachable without
        a Hermes-side allowlist and without per-user pairing codes. The only
        pairing is the initial owner/hub bootstrap on first connect; after that
        Band governs who can reach the agent.

        ``BAND_ALLOWED_USERS`` / ``BAND_ALLOW_ALL`` remain wired (register()) as
        an *optional* extra restriction an operator can layer on top: once
        either is set, the gateway's explicit allowlist check applies instead of
        this default-trust. The owner-only slash-command gate and the
        ``BAND_TOOL_OWNERS`` mutating-tool gate are independent and unaffected.
        """
        return True

    # ── Connection lifecycle ──────────────────────────────────────────────

    async def connect(self) -> bool:
        """Open the Band link, resolve identity, subscribe, start consuming."""
        # Reset per-connect hub failover state so a reconnect starts clean.
        self._hub_send_failures = 0
        self._failover_in_progress = False
        self._hub_failovers_done = 0

        if not BAND_AVAILABLE:
            missing_sdk_msg = (
                "band-sdk not installed. Directory plugin installs do not install "
                "Python dependencies; install into the gateway Python with: "
                "uv pip install --python <gateway-python> 'band-sdk>=1.0.0,<2.0.0'"
            )
            logger.error(
                "[band] %s",
                missing_sdk_msg,
            )
            self._set_fatal_error(
                "dependency_missing",
                missing_sdk_msg,
                retryable=False,
            )
            return False

        if not self._cfg_agent_id or not self._api_key:
            logger.error("[band] BAND_AGENT_ID and BAND_API_KEY must be set")
            self._set_fatal_error(
                "config_missing",
                "BAND_AGENT_ID and BAND_API_KEY must be set",
                retryable=False,
            )
            return False

        # Prevent two profiles from driving the same Band agent identity.
        # Best-effort: skip gracefully if the scoped-lock helper isn't present
        # (e.g. in some test harnesses).
        try:
            from gateway.status import acquire_scoped_lock

            acquired, existing = acquire_scoped_lock(
                "band", self._cfg_agent_id, metadata={"platform": "band"}
            )
            if not acquired:
                owner_pid = existing.get("pid") if isinstance(existing, dict) else None
                msg = (
                    f"Band agent {_short_id(self._cfg_agent_id)} already in use"
                    + (f" (PID {owner_pid})" if owner_pid else "")
                    + ". Stop the other gateway first."
                )
                logger.error("[band] %s", msg)
                self._set_fatal_error("band_lock", msg, retryable=False)
                return False
            self._lock_identity = self._cfg_agent_id
        except ImportError:
            self._lock_identity = None

        ws_url, rest_url = _derive_urls(self._base_url)

        try:
            self._link = BandLink(self._cfg_agent_id, self._api_key, ws_url, rest_url)
            await self._link.connect()

            # Resolve identity — the authoritative agent UUID + handle + owner.
            me = await self._link.rest.agent_api_identity.get_agent_me(
                request_options=DEFAULT_REQUEST_OPTIONS,
            )
            agent = getattr(me, "data", None)
            if agent is not None:
                self._agent_id = getattr(agent, "id", None) or self._cfg_agent_id
                self._handle = getattr(agent, "handle", "") or ""
                # Owner used later for guardrails — store it now (env override wins).
                self._owner_uuid = self._owner_uuid or getattr(agent, "owner_uuid", None)

            # Subscribe to agent-level room events (room_added / room_removed).
            await self._link.subscribe_agent_rooms(self._cfg_agent_id)

            # Subscribe to rooms already known to the server so we relay messages
            # in existing rooms without waiting for a room_added event.
            await self._subscribe_known_rooms()

            # Ensure the owner hub exists and is wired as the main channel.
            # Best-effort: a hub failure must never block messaging (the
            # command gate then stays fail-closed instead).
            try:
                await self._ensure_hub()
            except Exception as e:
                logger.warning("[band] Hub bootstrap failed — continuing without hub: %s", e)

            self._consumer_task = asyncio.create_task(self._consume())
            self._mark_connected()
            logger.info(
                "[band] Connected as agent %s (handle=%s, owner=%s)",
                _short_id(self._agent_id),
                self._handle or "<unknown>",
                _short_id(self._owner_uuid),
            )
            # Route A catch-up: drain each known room's server-side backlog of
            # messages missed while offline. Runs in the background so a large
            # backlog never delays connect() or blocks the live consumer.
            self._schedule_catch_up()
            return True
        except Exception as e:
            logger.error("[band] Failed to connect: %s", e)
            self._set_fatal_error("connect_failed", str(e), retryable=True)
            # Release the lock we may have taken so a retry isn't blocked.
            self._release_lock()
            return False

    async def _subscribe_known_rooms(self) -> None:
        """Subscribe to rooms the agent is already a participant of.

        Best-effort: paginate ``list_agent_chats`` and subscribe to each room.
        On any failure we fall back silently to room_added events.
        """
        try:
            page = 1
            while True:
                resp = await self._link.rest.agent_api_chats.list_agent_chats(
                    page=page,
                    request_options=DEFAULT_REQUEST_OPTIONS,
                )
                rooms = getattr(resp, "data", None) or []
                for room in rooms:
                    room_id = getattr(room, "id", None)
                    if room_id:
                        await self._link.subscribe_room(room_id)
                        self._known_rooms.add(room_id)
                meta = getattr(resp, "metadata", None)
                total_pages = getattr(meta, "total_pages", None)
                if total_pages is None or page >= total_pages:
                    break
                page += 1
        except Exception as e:
            logger.warning(
                "[band] Could not pre-subscribe known rooms (relying on room_added): %s",
                e,
            )

    async def disconnect(self) -> None:
        """Cancel the consumer, drop the link, release the scoped lock."""
        self._mark_disconnected()

        if self._catch_up_task and not self._catch_up_task.done():
            self._catch_up_task.cancel()
            try:
                await self._catch_up_task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.debug("[band] Catch-up task raised on shutdown: %s", e)
        self._catch_up_task = None

        if self._consumer_task and not self._consumer_task.done():
            self._consumer_task.cancel()
            try:
                await self._consumer_task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.debug("[band] Consumer task raised on shutdown: %s", e)
        self._consumer_task = None

        if self._link is not None:
            try:
                await self._link.disconnect()
            except Exception as e:
                logger.debug("[band] Error disconnecting link: %s", e)
        self._link = None

        self._release_lock()
        # _running is already cleared by _mark_disconnected() at the top.
        logger.info("[band] Disconnected")

    def _release_lock(self) -> None:
        """Release the scoped lock acquired in connect(), if any."""
        if not self._lock_identity:
            return
        try:
            from gateway.status import release_scoped_lock

            release_scoped_lock("band", self._lock_identity)
        except Exception:
            pass
        self._lock_identity = None

    # ── Owner hub (main channel) ──────────────────────────────────────────

    async def _ensure_hub(self) -> None:
        """Ensure the owner hub exists, is subscribed, and is the main channel.

        The hub is a private room of exactly {agent, owner}. Resolution order
        (idempotent across reconnects):

          1. Pinned id — ``BAND_HUB_ROOM`` env / ``extra["hub_room"]`` (set on
             a prior run by this method, or by the operator).
          2. Create — new room + owner participant + titling greeting.

        Existing rooms are NEVER adopted: a fresh install with no pinned id
        always gets its own dedicated room, so the hub can't collide with an
        unrelated owner↔agent conversation. Whatever resolves is persisted back
        to ``BAND_HUB_ROOM`` and wired as the platform home channel. Without a
        resolved owner there is no hub: Band slash commands then stay
        fail-closed (see _handle_message_created).
        """
        if not self._owner_uuid:
            logger.warning(
                "[band] Owner unresolved — hub disabled; Band slash commands will be refused"
            )
            return

        room_id = self._hub_room_id
        if not room_id:
            room_id = await self._create_hub_room()
        if not room_id:
            return

        self._hub_room_id = room_id
        self._known_rooms.add(room_id)
        try:
            # Idempotent for already-subscribed rooms; covers a pinned room
            # the list_agent_chats pre-subscribe pass may have missed.
            await self._link.subscribe_room(room_id)
        except Exception as e:
            logger.debug("[band] Hub subscribe failed (room_added will cover): %s", e)

        self._persist_hub_room(room_id)
        self._wire_home_channel(room_id)
        logger.info("[band] Hub ready: room %s (main channel)", _short_id(room_id))

    def _owner_label(self) -> str:
        """Best-effort ``@name`` for the owner, for the greeting body text.

        The owner is always notified via the mandatory ``mentions=[owner]``
        metadata; this is just the readable label in the message body. Scans
        the cached participant lists and last-human-sender cache for the
        owner's name/handle, falling back to ``there`` (no ``@``) when the
        owner's label isn't known yet (e.g. on a freshly created room).
        """
        if self._owner_uuid:
            for participants in self._participants_cache.values():
                for p in participants:
                    if p.get("id") == self._owner_uuid:
                        label = p.get("name") or p.get("handle")
                        if label:
                            return f"@{label}"
            for sender in self._last_human_sender.values():
                if sender.get("id") == self._owner_uuid:
                    label = sender.get("name") or sender.get("handle")
                    if label:
                        return f"@{label}"
        return "there"

    def _hub_greeting_body(self) -> str:
        """The owner-facing hub greeting sentence (no title line).

        Used verbatim as the adoption notice, and beneath the title line in a
        freshly created hub's titling greeting.
        """
        agent = self._handle or "Hermes"
        return (
            f"Hi {self._owner_label()}, this is your Hermes Agent, {agent}. "
            f"This chat is the '{_HUB_TITLE}', set as the Band main channel for "
            f"communication from the agent, straight to you."
        )

    def _build_hub_greeting(self) -> str:
        """First message for a freshly created hub.

        Leads with ``_HUB_TITLE`` so the server titles the new room, then the
        owner greeting body.
        """
        return f"{_HUB_TITLE}\n\n{self._hub_greeting_body()}"

    async def _create_hub_room(self) -> Optional[str]:
        """Create the hub: room → owner participant → titling greeting.

        The greeting doubles as the room title (the server derives titles
        from the first message) and @mentions the owner (mentions are
        mandatory on send). Returns the new room id, or None on failure.
        """
        try:
            created = await self._link.rest.agent_api_chats.create_agent_chat(
                chat=ChatRoomRequest(),
                request_options=DEFAULT_REQUEST_OPTIONS,
            )
            room_id = getattr(getattr(created, "data", None), "id", None)
            if not room_id:
                logger.warning("[band] Hub create returned no room id")
                return None

            await self._link.rest.agent_api_participants.add_agent_chat_participant(
                room_id,
                participant=ParticipantRequest(
                    participant_id=self._owner_uuid, role="member"
                ),
                request_options=DEFAULT_REQUEST_OPTIONS,
            )

            resp = await self._link.rest.agent_api_messages.create_agent_chat_message(
                chat_id=room_id,
                message=ChatMessageRequest(
                    content=self._build_hub_greeting(),
                    mentions=[ChatMessageRequestMentionsItem(id=self._owner_uuid)],
                ),
                request_options=DEFAULT_REQUEST_OPTIONS,
            )
            sent_id = getattr(getattr(resp, "data", None), "id", None)
            if sent_id:
                self._record_sent_id(sent_id)

            logger.info(
                "[band] Created hub room %s for owner %s",
                _short_id(room_id),
                _short_id(self._owner_uuid),
            )
            return room_id
        except Exception as e:
            logger.warning("[band] Hub creation failed: %s", e)
            return None

    def _persist_hub_room(self, room_id: str) -> None:
        """Persist the hub id so reconnects/restarts skip the scan/create.

        Mirrors into ``config.extra`` for in-process readers and writes
        ``BAND_HUB_ROOM`` to the Hermes .env (the same persistence /sethome
        uses). Best-effort: a write failure only costs a re-scan next start.
        """
        try:
            extra = getattr(self.config, "extra", None)
            if isinstance(extra, dict):
                extra["hub_room"] = room_id
        except Exception:
            pass
        try:
            from hermes_cli.config import save_env_value

            save_env_value("BAND_HUB_ROOM", room_id)
        except Exception as e:
            logger.debug("[band] Could not persist BAND_HUB_ROOM: %s", e)

    def _wire_home_channel(self, room_id: str) -> None:
        """Make the hub the platform main channel (cron/notification target).

        An explicit ``BAND_HOME_ROOM`` pointing elsewhere is an operator
        override (e.g. set via /sethome) and is respected; otherwise the hub
        wins. Mutates the live ``PlatformConfig`` exactly like /sethome does.
        """
        explicit = (os.getenv("BAND_HOME_ROOM") or "").strip()
        if explicit and explicit != room_id:
            logger.debug(
                "[band] BAND_HOME_ROOM=%s overrides hub as main channel",
                _short_id(explicit),
            )
            return
        try:
            self.config.home_channel = HomeChannel(
                platform=self.platform,
                chat_id=str(room_id),
                name="Hermes Hub",
            )
        except Exception as e:
            logger.debug("[band] Could not wire home channel: %s", e)

    # ── Inbound consumer ──────────────────────────────────────────────────

    async def _consume(self) -> None:
        """Consume platform events from the link and dispatch inbound messages.

        ``async for event in self._link`` blocks on the link's internal event
        queue.  Each event body is wrapped in try/except so one malformed event
        never kills the consumer; CancelledError exits cleanly.
        """
        try:
            async for event in self._link:
                try:
                    await self._handle_event(event)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.warning("[band] Error handling event: %s", e)
        except asyncio.CancelledError:
            return
        except Exception as e:
            # Iterator-level failure (PHX channel / queue error): the consumer is
            # dead and the adapter would silently go deaf. Signal a retryable
            # fatal error so the gateway runner can tear down and reconnect.
            logger.error("[band] Consumer loop exited unexpectedly: %s", e)
            self._set_fatal_error("consumer_died", str(e), retryable=True)
            await self._notify_fatal_error()

    async def _handle_event(self, event: Any) -> None:
        """Route a single platform event by type."""
        etype = getattr(event, "type", None)

        if etype == "message_created":
            await self._handle_message_created(event)
            return

        if etype == "reconnected":
            # The link dropped and re-subscribed its topics automatically; any
            # messages sent during the gap are unprocessed server-side. Drain
            # them via /next (Route A); the same drain also flags rooms with no
            # local session for rehydration (see _catch_up_all_rooms). Background,
            # so live events keep flowing.
            logger.info("[band] Link reconnected — scheduling catch-up drain")
            self._schedule_catch_up()
            return

        if etype == "room_added":
            room_id = getattr(event, "room_id", None)
            if room_id:
                await self._link.subscribe_room(room_id)
                # A room_added for a room we already knew (or recently left) is a
                # *re-join*: its local session was reset on the prior
                # room_removed, so flag it to rehydrate from Band context on the
                # next inbound message. Stay SILENT — events never proactively
                # wake the agent.
                if room_id in self._known_rooms or room_id in self._left_rooms:
                    self._left_rooms.discard(room_id)
                    self._known_rooms.add(room_id)
                    self._rehydrate_rooms.add(room_id)
                    logger.debug(
                        "[band] Re-joined known room %s — flagged for rehydration",
                        _short_id(room_id),
                    )
                else:
                    self._known_rooms.add(room_id)
                    logger.debug("[band] Subscribed to added room %s", _short_id(room_id))
            return

        if etype in ("room_removed", "room_deleted"):
            room_id = getattr(event, "room_id", None)
            if room_id:
                await self._link.unsubscribe_room(room_id)
                self._participants_cache.pop(room_id, None)
                self._last_human_sender.pop(room_id, None)
                self._reset_room_session(room_id)
                # Drop from the active set (so _known_rooms and the catch-up
                # drain don't grow/iterate without bound) but remember the room
                # in a capped _left_rooms so a later room_added is still seen as
                # a re-join. Eviction is best-effort: a forgotten room just
                # misses context rehydration on re-join.
                self._known_rooms.discard(room_id)
                self._left_rooms.add(room_id)
                if len(self._left_rooms) > _ROOM_CACHE_MAX:
                    for _ in range(_ROOM_CACHE_MAX // 2):
                        self._left_rooms.pop()
                logger.debug("[band] Unsubscribed from room %s", _short_id(room_id))
            return

        if etype in ("participant_added", "participant_removed"):
            await self._handle_participant_change(event, added=(etype == "participant_added"))
            return

        # TODO (contacts pass): handle contact_* events. Ignored this release.

    def _reset_room_session(self, room_id: str) -> None:
        """Clear the Hermes session for a removed/deleted room.

        Closing on leave discards the room's local transcript so a later re-join
        rebuilds context from the room's own server-side state (rehydration)
        rather than silently resuming stale history. Every Band room keys to a
        single ``_SESSION_CHAT_TYPE`` session (see the constant), so there is
        exactly one key to reset — no per-room chat-type bookkeeping, and
        ``group_sessions_per_user=False`` matches Band's locked one-channel model.
        """
        store = getattr(self, "_session_store", None)
        if not store:
            return
        try:
            key = build_session_key(
                SessionSource(
                    platform=self.platform,
                    chat_id=room_id,
                    chat_type=_SESSION_CHAT_TYPE,
                ),
                group_sessions_per_user=False,
            )
            store.reset_session(key)
        except Exception:
            pass

    async def _handle_participant_change(self, event: Any, added: bool) -> None:
        """Handle a participant_added / participant_removed event.

        Always invalidate the room's participant cache so the next send/fetch
        sees the new membership. Surface the change to the agent ONLY when the
        room already has an active session — events never proactively wake the
        agent (decision #3). For cold rooms the change is folded in silently at
        the next real turn via the refreshed cache.
        """
        room_id = getattr(event, "room_id", None)
        if not room_id:
            return

        # Force a refetch on next access — membership just changed.
        self._participants_cache.pop(room_id, None)

        if not self._has_active_session(room_id):
            logger.debug(
                "[band] Participant %s in cold room %s — cache invalidated, no wake",
                "added" if added else "removed",
                _short_id(room_id),
            )
            return

        payload = getattr(event, "payload", None)
        name = getattr(payload, "name", None) or _short_id(getattr(payload, "id", None))
        verb = "joined" if added else "left"
        await self.handle_message(
            MessageEvent(
                text=f"[System] {name} {verb} this room.",
                message_type=MessageType.TEXT,
                source=self.build_source(chat_id=room_id, chat_type=_SESSION_CHAT_TYPE),
                internal=True,
                raw_message=payload,
            )
        )

    async def _handle_message_created(self, event: Any) -> bool:
        """Normalize an inbound Band message into a Hermes MessageEvent.

        Returns ``True`` when the message was forwarded to the gateway (its ack
        is then driven by the processing-lifecycle hooks), ``False`` when it was
        filtered/dropped. The caught-up drain uses this to decide whether to
        make a drained message terminal itself (see ``_drain_room``); the live
        consumer ignores the return value.
        """
        payload = getattr(event, "payload", None)
        if payload is None:
            return False

        room_id = getattr(event, "room_id", None) or getattr(payload, "chat_room_id", None)
        if not room_id:
            return False

        msg_id = getattr(payload, "id", None)
        content = getattr(payload, "content", None) or ""
        # Band embeds the addressed mention inline at the FRONT of the content
        # (``@[[<agent-id>]] /help``). The gateway detects slash commands with
        # ``text.startswith("/")``, so strip our own leading self-mention here —
        # otherwise an addressed command never dispatches (the mention precedes
        # the slash). Also de-noises ordinary prose.
        content = self._strip_self_mention(content)
        message_type = getattr(payload, "message_type", "") or ""
        sender_id = getattr(payload, "sender_id", None)
        sender_type = getattr(payload, "sender_type", "") or ""
        sender_name = getattr(payload, "sender_name", None)

        # Self-filter: skip our own agent messages (primary), and use the
        # sent-id set as a backstop in case the platform echoes a post we made.
        # Neither is offered by /next (the agent's own posts don't @mention it),
        # so there is nothing to ack — just drop.
        if sender_type == "Agent" and sender_id == self._agent_id:
            return False
        if msg_id and msg_id in self._sent_ids:
            return False

        # Inbound dedup: if we already forwarded this id this process-lifetime
        # (live-vs-catch-up race, or a server re-offer), its first turn owns the
        # ack — don't double-process. Report it as forwarded so the drain leaves
        # settling to that first turn.
        if msg_id and msg_id in self._seen_inbound_ids:
            return True

        # Only relay user/text content. tool_call / thought / tool_result /
        # error / task etc. are event-type messages, not conversational text.
        # /next returns text only, so these never appear in a drain.
        if message_type and message_type != "text":
            logger.debug(
                "[band] Skipping non-text message_type=%s in room %s",
                message_type,
                _short_id(room_id),
            )
            return False

        # Participants drive @mention-handle resolution and last-human-sender
        # tracking (fetched on first sight). chat_type is NOT derived from them:
        # every Band room is one shared, mention-gated session keyed on room_id
        # (see _SESSION_CHAT_TYPE) so the conversation can't re-key when the
        # roster changes.
        participants = await self._get_participants(room_id)
        chat_type = _SESSION_CHAT_TYPE

        # Track the last human sender so replies can @mention them (mentions are
        # mandatory on send).
        if sender_id and sender_type != "Agent":
            handle = self._handle_for_participant(participants, sender_id)
            self._last_human_sender[room_id] = {
                "id": sender_id,
                "handle": handle,
                "name": sender_name,
            }
            self._cap_cache(self._last_human_sender, _ROOM_CACHE_MAX)

        # Owner command gate: slash commands are accepted ONLY from the owner,
        # in any Band room. Command-shaped text from anyone else is dropped
        # here — before the gateway ever sees it — silently for other agents
        # (a notice would invite bot↔bot ping-pong), with a one-time per-room
        # notice for humans. Fail-closed: no resolved owner means no Band
        # slash commands at all. Plain chat is unaffected (runs after the
        # last-human-sender update so the notice can @mention the sender).
        is_command = self._is_command_text(content)
        if is_command and not self._is_owner_command(sender_id):
            if sender_type != "Agent":
                await self._notify_command_blocked(room_id)
            # A blocked command is command-shaped @mention text, so /next WOULD
            # re-offer it. We've adjudicated it (dropped) — mark it processed so
            # the server cursor advances and it isn't redelivered on reconnect.
            await self._ack_consumed(room_id, msg_id)
            return False

        # Gating: Band has no DMs — every room is a mention-gated group room, so
        # the agent wakes ONLY when it's @mentioned. The platform already routes
        # by mention (/next offers only mentioned messages); we mirror that on the
        # live path rather than inventing our own routing — no hub bypass, no
        # active-session stickiness, so live and catch-up behave identically. The
        # one always-pass case is a validated owner slash command (any
        # command-shaped text still here has cleared the owner gate above), so the
        # owner never has to @mention the agent to run a command.
        if not (is_command or self._is_agent_mentioned(payload)):
            logger.debug(
                "[band] Ignoring un-addressed message in room %s",
                _short_id(room_id),
            )
            # Not an @mention, so /next never offers it (its filter is
            # mention-only) — nothing to ack, just drop.
            return False

        room_name = self._room_name_for(room_id) or room_id

        source = self.build_source(
            chat_id=room_id,
            chat_name=room_name,
            chat_type=chat_type,
            user_id=sender_id,
            user_name=sender_name,
            thread_id=None,  # Band uses rooms, not threads — always None.
        )

        # Rehydration: if this room is flagged — re-joined (room_removed reset
        # its session) or returning with no local session (agent restart / lost
        # DB, flagged by _catch_up_all_rooms) — pull recent agent-relevant Band
        # context into channel_context so the session rebuilds from the room's
        # server-side state rather than empty history. Best-effort — never block
        # the message on a hydration failure; the flag is consumed once
        # regardless so we don't refetch on every subsequent message.
        channel_context: Optional[str] = None
        if room_id in self._rehydrate_rooms:
            self._rehydrate_rooms.discard(room_id)
            channel_context = await self._fetch_rehydration_context(room_id)

        if msg_id:
            self._seen_inbound_ids.add(msg_id)
            if len(self._seen_inbound_ids) > _SENT_IDS_MAX:
                for _ in range(_SENT_IDS_MAX // 2):
                    self._seen_inbound_ids.pop()

        await self.handle_message(
            MessageEvent(
                text=content,
                message_type=MessageType.TEXT,
                source=source,
                message_id=msg_id,
                raw_message=payload,
                channel_context=channel_context,
            )
        )
        return True

    # ── Route A: server-cursor ack + missed-message catch-up ──────────────
    #
    # The Band platform owns a per-agent, per-message read cursor (the message
    # delivery-status state machine). We advance it by marking messages
    # processing/processed/failed via the SDK link helpers, and on (re)connect
    # we drain each room's unprocessed backlog through /next. This is the
    # link-level equivalent of the SDK runtime's ExecutionContext sync loop —
    # used directly because Hermes's handle_message is fire-and-forget (it
    # returns before the turn completes), which is incompatible with the
    # runtime's ack-on-handler-return contract. Instead we ack from the
    # gateway's processing-lifecycle hooks, which fire at true turn completion.

    async def _ack_consumed(self, room_id: str, msg_id: Optional[str]) -> None:
        """Mark a message processed (terminal, best-effort).

        For messages adjudicated without a full turn (a blocked command) and for
        caught-up messages that gating dropped. Advances the server cursor so
        /next won't re-offer the message.
        """
        if not msg_id or not self._link:
            return
        try:
            await self._link.mark_processed(room_id, msg_id)
        except Exception as e:
            logger.debug("[band] mark_processed failed for msg %s: %s", _short_id(msg_id), e)

    async def on_processing_start(self, event: MessageEvent) -> None:
        """Claim an inbound message as 'processing' when its turn begins.

        Excludes it from /next while the agent works and leaves a crash mid-turn
        recoverable: an interrupted message stays 'processing' and is re-picked
        by the stale-processing sweep on the next connect. Internal/synthetic
        events (participant notices) carry no Band id and are skipped.
        """
        if getattr(event, "internal", False):
            return
        msg_id = getattr(event, "message_id", None)
        room_id = getattr(getattr(event, "source", None), "chat_id", None)
        if not msg_id or not room_id or not self._link:
            return
        try:
            await self._link.mark_processing(room_id, msg_id)
        except Exception as e:
            logger.debug("[band] mark_processing failed for msg %s: %s", _short_id(msg_id), e)

    async def on_processing_complete(
        self, event: MessageEvent, outcome: ProcessingOutcome
    ) -> None:
        """Settle an inbound message's server-side state when its turn ends.

        SUCCESS / CANCELLED → processed (consumed; never re-deliver — a cancelled
        turn was superseded by a newer message or an intentional /stop).
        FAILURE → failed, so the server may re-offer it on a later /next drain
        for another attempt. Internal events are skipped.
        """
        if getattr(event, "internal", False):
            return
        msg_id = getattr(event, "message_id", None)
        room_id = getattr(getattr(event, "source", None), "chat_id", None)
        if not msg_id or not room_id or not self._link:
            return
        try:
            if outcome == ProcessingOutcome.FAILURE:
                await self._link.mark_failed(room_id, msg_id, "agent processing failed")
            else:
                await self._link.mark_processed(room_id, msg_id)
        except Exception as e:
            logger.debug(
                "[band] ack (%s) failed for msg %s: %s",
                getattr(outcome, "value", outcome),
                _short_id(msg_id),
                e,
            )

    def _schedule_catch_up(self) -> None:
        """(Re)start the background catch-up drain for all known rooms.

        Idempotent: if a drain is already in flight it is left to finish (it
        polls /next live, so it naturally covers anything that arrived since it
        started). Called on connect and on every link reconnect.
        """
        if self._catch_up_task and not self._catch_up_task.done():
            return
        self._catch_up_task = asyncio.create_task(self._catch_up_all_rooms())

    async def _catch_up_all_rooms(self) -> None:
        """Drain every known room's missed-message backlog (Route A).

        On (re)connect: (0) flag rooms whose local history is gone for
        rehydration, (1) re-pick anything left 'processing' by a prior crash,
        then (2) drain the unprocessed backlog via /next. Each message flows
        through the same gate/normalize path as a live one and is acked via the
        processing-lifecycle hooks. Best-effort and per-room isolated — one
        room's failure never blocks the others or live consumption.
        """
        if not self._link or not getattr(self, "_message_handler", None):
            return
        for room_id in list(self._known_rooms):
            if not self._link:
                return
            # Agent-lifecycle rehydration: this drain runs on every (re)connect,
            # so it's where a returning agent recovers context — not just on a
            # room re-join (room_removed→room_added, handled in _handle_event).
            # If the room has no local session (fresh deploy, lost/migrated DB,
            # or first run after the agent was down), flag it so the next message
            # we process — stale sweep, /next drain, or a later live event —
            # rebuilds from the room's server-side history via
            # _fetch_rehydration_context rather than answering the backlog cold.
            # A room with an intact local session is skipped (its history is in
            # the store); a room that genuinely has no history yields None and
            # the flag clears harmlessly on first use.
            if not self._has_active_session(room_id):
                self._rehydrate_rooms.add(room_id)
            # Crash recovery: /next skips messages with an active processing
            # attempt, so stuck-'processing' ones from a prior incarnation must
            # be swept explicitly and re-driven.
            try:
                stale = await self._link.get_stale_processing_messages(room_id)
            except Exception as e:
                stale = []
                logger.debug(
                    "[band] stale-processing sweep failed for room %s: %s",
                    _short_id(room_id), e,
                )
            for msg in stale or []:
                await self._dispatch_caught_up(room_id, msg)
            await self._drain_room(room_id)

    async def _drain_room(self, room_id: str) -> None:
        """Pull and dispatch a room's unprocessed backlog until /next is empty.

        Each message is claimed ('processing') before the next /next call so the
        cursor advances even though dispatch is fire-and-forget; forwarded
        messages are later settled by the completion hook, while gating-dropped
        ones are made terminal here. A per-pass ``seen`` set is a backstop
        against a server re-offering the same id (which would otherwise spin).
        """
        seen: set = set()
        idless_skips = 0
        while self._link is not None:
            try:
                msg = await self._link.get_next_message(room_id)
            except Exception as e:
                logger.debug(
                    "[band] /next drain error for room %s: %s", _short_id(room_id), e
                )
                return
            if msg is None:
                return
            mid = getattr(msg, "id", None)
            if not mid:
                # Can't claim/ack an id-less message, but skip it and keep
                # draining the rest of the backlog rather than abandoning the
                # whole room. Cap consecutive skips so a server re-offering an
                # un-ackable message can't spin this loop forever.
                idless_skips += 1
                if idless_skips >= _MAX_DRAIN_IDLESS_SKIPS:
                    logger.warning(
                        "[band] Too many id-less messages draining room %s — stopping",
                        _short_id(room_id),
                    )
                    return
                logger.debug(
                    "[band] /next returned an id-less message for room %s — skipping",
                    _short_id(room_id),
                )
                continue
            idless_skips = 0
            if mid in seen:
                await self._ack_consumed(room_id, mid)
                continue
            seen.add(mid)
            # Claim before the next fetch (dispatch returns before the turn
            # runs, so without this /next would re-return the same message).
            try:
                await self._link.mark_processing(room_id, mid)
            except Exception:
                pass
            forwarded = await self._dispatch_caught_up(room_id, msg)
            if not forwarded:
                # Gating dropped it — settle now so it isn't re-offered.
                await self._ack_consumed(room_id, mid)

    async def _dispatch_caught_up(self, room_id: str, msg: Any) -> bool:
        """Feed a caught-up ``PlatformMessage`` through the live normalize/gate
        path, wrapping it to mimic the SDK's live ``message_created`` event so
        the handler is reused verbatim. Returns whether it was forwarded.
        """
        event = SimpleNamespace(type="message_created", room_id=room_id, payload=msg)
        try:
            return await self._handle_message_created(event)
        except Exception as e:
            logger.debug(
                "[band] caught-up dispatch failed for room %s: %s",
                _short_id(room_id), e,
            )
            return False

    async def _fetch_rehydration_context(self, room_id: str) -> Optional[str]:
        """Pull recent agent-relevant Band messages to rebuild a room's context.

        Used when local history is missing — a room re-join or an agent that
        came back online with no stored session. Returns a plain-text blob
        suitable for ``MessageEvent.channel_context``, or None when nothing
        useful was retrieved. Best-effort: any failure is swallowed (logged at
        debug) so a hydration miss never blocks the triggering message.
        """
        try:
            ctx = await self._link.rest.agent_api_context.get_agent_chat_context(
                chat_id=room_id,
                request_options=DEFAULT_REQUEST_OPTIONS,
            )
        except Exception as e:
            logger.debug(
                "[band] Rehydration context fetch failed for room %s: %s",
                _short_id(room_id),
                e,
            )
            return None

        items = getattr(ctx, "data", None) or []
        lines: List[str] = []
        for item in items:
            message_type = getattr(item, "message_type", "text") or "text"
            if message_type != "text":
                continue
            text = getattr(item, "content", None) or ""
            if not text.strip():
                continue
            who = (
                getattr(item, "sender_name", None)
                or getattr(item, "name", None)
                or getattr(item, "sender_type", None)
                or "?"
            )
            lines.append(f"{who}: {text.strip()}")

        if not lines:
            return None
        logger.debug(
            "[band] Rehydrated %d context message(s) for room %s",
            len(lines),
            _short_id(room_id),
        )
        return "Recent room history (recovered from Band):\n" + "\n".join(lines)

    # ── Inbound helpers ───────────────────────────────────────────────────

    def _is_agent_mentioned(self, payload: Any) -> bool:
        """Return True if the agent id/handle is in payload.metadata.mentions.

        Handles both the live SDK payload (metadata + mentions as objects) and a
        caught-up ``PlatformMessage`` whose ``metadata`` is a plain dict with
        ``mentions`` as a list of dicts.
        """
        metadata = getattr(payload, "metadata", None)
        if isinstance(metadata, dict):
            mentions = metadata.get("mentions") or []
        else:
            mentions = getattr(metadata, "mentions", None) or []
        for m in mentions:
            if isinstance(m, dict):
                mid = m.get("id")
                mhandle = m.get("handle")
            else:
                mid = getattr(m, "id", None)
                mhandle = getattr(m, "handle", None)
            if mid and mid == self._agent_id:
                return True
            if mhandle and self._handle and mhandle == self._handle:
                return True
        return False

    def _strip_self_mention(self, content: str) -> str:
        """Remove leading ``@[[<agent>]]`` self-mentions from inbound content.

        Band renders an addressed mention as ``@[[<id-or-handle>]]`` inline at
        the start of the message. The gateway's command detector keys on a
        leading "/", so ``@[[agent]] /help`` would never be seen as a command.
        Strip any run of the agent's own leading mention tokens (matched by the
        agent id, the configured id, or the handle) plus surrounding
        whitespace, so a command — or clean prose — leads the text. Other
        participants' mentions are left untouched.
        """
        if not content:
            return content
        text = content.lstrip()
        idents = [i for i in (self._agent_id, self._cfg_agent_id, self._handle) if i]
        if not idents:
            return text
        changed = True
        while changed:
            changed = False
            for ident in idents:
                token = f"@[[{ident}]]"
                if text.startswith(token):
                    text = text[len(token):].lstrip()
                    changed = True
        return text

    @staticmethod
    def _is_command_text(text: str) -> bool:
        """Whether the gateway would dispatch ``text`` as a slash command.

        Mirrors ``MessageEvent.is_command`` + ``get_command`` parsing: a
        leading "/" and a first token that is a valid command name (no "/"
        inside it — so file paths like ``/usr/bin/ls`` stay plain chat).
        """
        if not text or not text.startswith("/"):
            return False
        first = text.split(maxsplit=1)[0][1:]
        if "@" in first:
            first = first.split("@", 1)[0]
        return bool(first) and "/" not in first

    def _is_owner_command(self, sender_id: Any) -> bool:
        """True when a slash command is allowed: from the owner, any room."""
        return bool(self._owner_uuid and sender_id == self._owner_uuid)

    async def _notify_command_blocked(self, room_id: str) -> None:
        """Drop a non-owner slash command, with a one-time per-room notice."""
        if room_id in self._cmd_notice_rooms:
            logger.debug(
                "[band] Dropped non-owner slash command in room %s", _short_id(room_id)
            )
            return
        self._cmd_notice_rooms.add(room_id)
        # Bound like _sent_ids: evict half (arbitrary) when over — a re-notice
        # after eviction is harmless.
        if len(self._cmd_notice_rooms) > _ROOM_CACHE_MAX:
            for _ in range(_ROOM_CACHE_MAX // 2):
                self._cmd_notice_rooms.pop()
        try:
            await self.send(room_id, _OWNER_COMMAND_NOTICE)
        except Exception as e:
            logger.debug("[band] Could not send command-gate notice: %s", e)

    def _has_active_session(self, room_id: str) -> bool:
        """Whether the room has an active Hermes session (group gating).

        Uses the session store wired onto the adapter via ``set_session_store``
        (the same mechanism Slack uses for un-mentioned thread replies). Band
        rooms aren't threaded, so the session key is built without a thread_id.
        Returns False if the store isn't available.
        """
        session_store = getattr(self, "_session_store", None)
        if not session_store:
            return False
        try:
            from gateway.session import SessionSource, build_session_key

            # Use the same constant chat_type every other call site uses so this
            # key matches how the room's session was stored. Band has one shared
            # session per room regardless of participant count.
            source = SessionSource(
                platform=self.platform,
                chat_id=room_id,
                chat_type=_SESSION_CHAT_TYPE,
            )
            store_cfg = getattr(session_store, "config", None)
            gspu = getattr(store_cfg, "group_sessions_per_user", False) if store_cfg else False
            session_key = build_session_key(source, group_sessions_per_user=gspu)
            session_store._ensure_loaded()
            return session_key in session_store._entries
        except Exception:
            return False

    def _chat_type_label(self, chat_id: str) -> str:
        """Cosmetic 'hub'/'group' label for ``get_chat_info`` reporting ONLY.

        Band has no DMs — every room is a group chat regardless of participant
        count — so the only distinction worth surfacing is the owner's hub vs a
        regular chat. Returns 'hub' for the hub room, else 'group'.

        Do NOT use this for session keys, the message source, or gating: those
        all use the constant ``_SESSION_CHAT_TYPE`` so a roster change can never
        re-key a room. This label is informational situational awareness for the
        agent, not a routing input.
        """
        if self._hub_room_id and chat_id == self._hub_room_id:
            return "hub"
        return "group"

    @staticmethod
    def _handle_for_participant(
        participants: List[Dict[str, Any]], participant_id: str
    ) -> Optional[str]:
        for p in participants:
            if p.get("id") == participant_id:
                return p.get("handle")
        return None

    @staticmethod
    def _cap_cache(cache: Dict[str, Any], max_size: int) -> None:
        """Bound a per-room cache: when it grows past ``max_size``, drop the
        oldest-inserted entries down to half capacity. Entries are re-fetched
        on demand, so eviction degrades gracefully (a cache miss, not an error).
        """
        if len(cache) > max_size:
            for key in list(cache.keys())[: len(cache) - max_size // 2]:
                cache.pop(key, None)

    def _room_name_for(self, room_id: str) -> Optional[str]:
        """Best-effort human room name. Band participant data has no room title,
        so we currently return None (caller falls back to room_id)."""
        # TODO (memory/hydration pass): cache room titles from list_agent_chats /
        # room_added payloads so chat_name surfaces a friendly label.
        return None

    async def _get_participants(self, room_id: str) -> List[Dict[str, Any]]:
        """Return cached participants for a room, fetching on first sight."""
        cached = self._participants_cache.get(room_id)
        if cached is not None:
            return cached

        participants: List[Dict[str, Any]] = []
        try:
            resp = await self._link.rest.agent_api_participants.list_agent_chat_participants(
                chat_id=room_id,
                request_options=DEFAULT_REQUEST_OPTIONS,
            )
            data = getattr(resp, "data", None) or []
            participants = [
                {
                    "id": getattr(p, "id", None),
                    "name": getattr(p, "name", None),
                    "handle": getattr(p, "handle", None),
                    "type": getattr(p, "type", None),
                }
                for p in data
            ]
        except Exception as e:
            logger.warning(
                "[band] Failed to fetch participants for room %s: %s",
                _short_id(room_id),
                e,
            )
        self._participants_cache[room_id] = participants
        self._cap_cache(self._participants_cache, _ROOM_CACHE_MAX)
        return participants

    # ── Sending ───────────────────────────────────────────────────────────

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """Post a message to a Band room.

        ``chat_id`` is the room id. Mentions are MANDATORY (the API rejects
        empty mention lists), so we mention the cached last-human-sender for the
        room, falling back to all non-agent participants. Band rooms aren't
        threaded, so ``reply_to`` is ignored.
        """
        if not self._link:
            return SendResult(success=False, error="Not connected", retryable=True)

        room_id = chat_id

        mention_items = await self._build_mentions(room_id)
        if not mention_items:
            # API requires ≥1 mention; without a recipient we cannot post.
            logger.warning(
                "[band] No mentionable recipient for room %s — dropping send",
                _short_id(room_id),
            )
            return SendResult(
                success=False,
                error="No mentionable recipient (Band requires >=1 mention)",
                retryable=False,
            )

        chunks = self.truncate_message(content, self.MAX_MESSAGE_LENGTH)

        last_id: Optional[str] = None
        last_resp: Any = None
        continuation: List[str] = []
        # Mentions are repeated on every chunk: the Band API mandates >=1 mention
        # per message, so continuation chunks cannot drop them. Multi-chunk
        # replies (> MAX_MESSAGE_LENGTH) are rare; if duplicate notifications
        # become a problem, confirm whether the API permits mention-less
        # continuation messages before changing this. (smoke-test item)
        try:
            for chunk in chunks:
                resp = await self._link.rest.agent_api_messages.create_agent_chat_message(
                    chat_id=room_id,
                    message=ChatMessageRequest(content=chunk, mentions=mention_items),
                    request_options=DEFAULT_REQUEST_OPTIONS,
                )
                last_resp = resp
                sent_id = getattr(getattr(resp, "data", None), "id", None)
                if sent_id:
                    if last_id is not None:
                        continuation.append(last_id)
                    last_id = sent_id
                    self._record_sent_id(sent_id)
        except Exception as e:
            logger.error("[band] Failed to send to room %s: %s", _short_id(room_id), e)
            await self._record_hub_send(room_id, ok=False)
            return SendResult(
                success=False,
                error=str(e),
                retryable=self._is_retryable(e),
            )

        await self._record_hub_send(room_id, ok=True)
        return SendResult(
            success=True,
            message_id=last_id,
            raw_response=last_resp,
            continuation_message_ids=tuple(continuation),
        )

    async def _record_hub_send(self, room_id: str, *, ok: bool) -> None:
        """Track hub send health and fail over after repeated failures.

        Only the hub room is tracked — failover protects the main channel.
        A successful hub send clears the consecutive-failure counter; a failed
        one increments it, and once it reaches ``_hub_failover_threshold`` the
        adapter creates a fresh owner hub and re-wires it as the main channel
        (see ``_failover_hub``). Other rooms are ignored, and the not-connected
        / no-mentionable-recipient early returns in ``send`` never reach here
        (those are adapter / config issues, not hub-health signals).
        """
        if not self._hub_room_id or room_id != self._hub_room_id:
            return
        if ok:
            self._hub_send_failures = 0
            return
        self._hub_send_failures += 1
        logger.warning(
            "[band] Hub send failed (%d/%d consecutive) for room %s",
            self._hub_send_failures,
            self._hub_failover_threshold,
            _short_id(room_id),
        )
        if (
            self._hub_send_failures >= self._hub_failover_threshold
            and not self._failover_in_progress
            and self._owner_uuid
            and self._hub_failovers_done < self._hub_failover_max_per_connect
        ):
            await self._failover_hub()

    async def _failover_hub(self) -> None:
        """Replace a failing hub with a fresh owner room, wired as main channel.

        Always creates a brand-new {agent, owner} room (never adopts — the
        existing hub is the broken one), greeting the owner via
        ``_build_hub_greeting``. On success the new room becomes the hub
        (persisted + wired as the Band main channel, like the bootstrap path);
        on failure the old hub id is kept as the best available target. The
        consecutive-failure counter is reset either way (in ``finally``), so a
        failed create only re-arms after another ``_hub_failover_threshold``
        failures — a platform-wide outage can't spin up rooms without bound
        (successful failovers are additionally capped per connect).

        The triggering message is not auto-resent; subsequent sends go to the
        new hub, and the owner learns of the move from its greeting.
        """
        self._failover_in_progress = True
        old_hub = self._hub_room_id
        try:
            logger.warning(
                "[band] Hub %s failing — failing over to a fresh owner room",
                _short_id(old_hub),
            )
            new_hub = await self._create_hub_room()
            if not new_hub:
                logger.warning(
                    "[band] Hub failover could not create a new room — keeping %s",
                    _short_id(old_hub),
                )
                return
            self._hub_room_id = new_hub
            self._known_rooms.add(new_hub)
            try:
                await self._link.subscribe_room(new_hub)
            except Exception as e:
                logger.debug(
                    "[band] Failover hub subscribe failed (room_added will cover): %s", e
                )
            self._persist_hub_room(new_hub)
            self._wire_home_channel(new_hub)
            self._hub_failovers_done += 1
            logger.info(
                "[band] Hub failover: %s → %s (new main channel)",
                _short_id(old_hub),
                _short_id(new_hub),
            )
        except Exception as e:
            logger.warning("[band] Hub failover failed: %s", e)
        finally:
            self._hub_send_failures = 0
            self._failover_in_progress = False

    async def _build_mentions(self, room_id: str) -> List[Any]:
        """Build the mandatory mention list for a send.

        Prefer the cached last-human-sender; otherwise mention every non-agent
        participant in the room.
        """
        items: List[Any] = []

        last = self._last_human_sender.get(room_id)
        if last and last.get("id"):
            items.append(
                ChatMessageRequestMentionsItem(id=last["id"], handle=last.get("handle"))
            )
            return items

        participants = await self._get_participants(room_id)
        for p in participants:
            pid = p.get("id")
            if not pid or pid == self._agent_id:
                continue
            if p.get("type") == "Agent":
                continue
            items.append(ChatMessageRequestMentionsItem(id=pid, handle=p.get("handle")))
        return items

    def _record_sent_id(self, sent_id: str) -> None:
        """Track a sent message id for the inbound self-echo backstop.

        Caps the set at ``_SENT_IDS_MAX``; evicts half (arbitrary) when over.
        """
        if len(self._sent_ids) >= _SENT_IDS_MAX:
            target = _SENT_IDS_MAX // 2
            for _ in range(max(0, len(self._sent_ids) - target)):
                self._sent_ids.pop()
        self._sent_ids.add(sent_id)

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        """Best-effort transient-error classification for SendResult.retryable."""
        status = getattr(exc, "status_code", None)
        if isinstance(status, int):
            return status == 429 or status >= 500
        text = str(exc).lower()
        return any(
            term in text
            for term in ("timeout", "timed out", "connection", "temporarily", "unavailable")
        )

    # ── Chat info ─────────────────────────────────────────────────────────

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        """Return ``{"name", "type"}`` for a room from cached participants."""
        participants = self._participants_cache.get(chat_id)
        if participants is None:
            try:
                participants = await self._get_participants(chat_id)
            except Exception:
                participants = []
        if participants:
            return {
                "name": self._room_name_for(chat_id) or chat_id,
                "type": self._chat_type_label(chat_id),
            }
        # Participant fetch failed. Band has no DMs, so "group" is the correct
        # fall-through type (never the base contract's "unknown").
        return {"name": chat_id, "type": "group"}


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

def check_band_requirements() -> bool:
    """Check (and lazily bind) the Band SDK symbols this adapter needs.

    Self-contained lazy import to honor the zero-core-edits constraint: it
    ``global``s the SDK symbols + the ``BAND_AVAILABLE`` flag, imports the
    specific names inside the function, binds them to module globals, and
    returns True; on ImportError it returns False.

    To enable Hermes auto-install, a ``'platform.band': ('band-sdk>=1.0.0,<2.0.0',)``
    entry could be added to tools/lazy_deps.py and this could use
    ``tools.lazy_deps.ensure_and_bind``; deferred to keep zero core edits.
    """
    global BAND_AVAILABLE, BandLink, BandMessageEvent
    global RoomAddedEvent, RoomRemovedEvent, RoomDeletedEvent
    global ChatMessageRequest, ChatMessageRequestMentionsItem
    global ChatRoomRequest, ParticipantRequest, DEFAULT_REQUEST_OPTIONS

    if BAND_AVAILABLE:
        return True
    try:
        from band.platform.link import BandLink as _BandLink
        from band.platform.event import (
            MessageEvent as _BandMessageEvent,
            RoomAddedEvent as _RoomAddedEvent,
            RoomRemovedEvent as _RoomRemovedEvent,
            RoomDeletedEvent as _RoomDeletedEvent,
        )
        from band.client.rest import (
            ChatMessageRequest as _ChatMessageRequest,
            ChatMessageRequestMentionsItem as _ChatMessageRequestMentionsItem,
            ChatRoomRequest as _ChatRoomRequest,
            ParticipantRequest as _ParticipantRequest,
            DEFAULT_REQUEST_OPTIONS as _DEFAULT_REQUEST_OPTIONS,
        )
    except ImportError:
        return False

    BandLink = _BandLink
    BandMessageEvent = _BandMessageEvent
    RoomAddedEvent = _RoomAddedEvent
    RoomRemovedEvent = _RoomRemovedEvent
    RoomDeletedEvent = _RoomDeletedEvent
    ChatMessageRequest = _ChatMessageRequest
    ChatMessageRequestMentionsItem = _ChatMessageRequestMentionsItem
    ChatRoomRequest = _ChatRoomRequest
    ParticipantRequest = _ParticipantRequest
    DEFAULT_REQUEST_OPTIONS = _DEFAULT_REQUEST_OPTIONS
    BAND_AVAILABLE = True
    return True


def _is_connected(config) -> bool:
    """Check whether Band is minimally configured (env or config.yaml)."""
    extra = getattr(config, "extra", {}) or {}
    agent_id = os.getenv("BAND_AGENT_ID") or extra.get("agent_id", "")
    api_key = os.getenv("BAND_API_KEY") or extra.get("api_key", "")
    return bool(agent_id and api_key)


def validate_config(config) -> bool:
    """Validate that the platform config has enough info to connect."""
    return _is_connected(config)


def _env_enablement() -> dict | None:
    """Seed ``PlatformConfig.extra`` from env vars during gateway config load.

    Called by the platform registry's env-enablement hook BEFORE adapter
    construction, so ``gateway status`` reflects env-only configuration without
    opening a WebSocket. Returns ``None`` when Band isn't minimally configured;
    the caller skips auto-enabling.

    The API key is seeded into ``extra`` for back-compat with config.yaml
    users; env reads at construct time still win and the key is never logged.
    """
    agent_id = os.getenv("BAND_AGENT_ID", "").strip()
    api_key = os.getenv("BAND_API_KEY", "").strip()
    if not (agent_id and api_key):
        return None

    seed: dict = {
        "agent_id": agent_id,
        "api_key": api_key,
    }
    base_url = os.getenv("BAND_BASE_URL", "").strip()
    if base_url:
        seed["base_url"] = base_url
    owner_id = os.getenv("BAND_OWNER_ID", "").strip()
    if owner_id:
        seed["owner_id"] = owner_id

    # A Band room is one shared channel (locked decision #5): every participant
    # feeds the same session rather than splitting into per-user threads. Seed
    # group_sessions_per_user into extra so handle_message picks it up from
    # PlatformConfig.extra; default False, overridable via env for parity tests.
    # (BAND_TOOL_OWNERS is read directly by the tools — no seed needed.)
    gspu_raw = os.getenv("BAND_GROUP_SESSIONS_PER_USER", "").strip().lower()
    seed["group_sessions_per_user"] = gspu_raw in ("1", "true", "yes", "on")

    # Hub pinning + main-channel seeding. BAND_HUB_ROOM is written back by the
    # adapter after the first hub bootstrap; BAND_HOME_ROOM (e.g. via /sethome)
    # is an operator override for the cron/notification target. Either one
    # seeds home_channel at config load so ``deliver=band`` resolves before —
    # and without — a live connect.
    hub_room = os.getenv("BAND_HUB_ROOM", "").strip()
    if hub_room:
        seed["hub_room"] = hub_room
    home_room = os.getenv("BAND_HOME_ROOM", "").strip() or hub_room
    if home_room:
        seed["home_channel"] = {
            "chat_id": home_room,
            "name": "Hermes Hub" if home_room == hub_room else "Band Home",
        }

    # TODO (memory pass): surface BAND_MEMORY_PRELOAD / BAND_MEMORY_WRITETHROUGH
    #   and BAND_CONTACT_STRATEGY here once those features land.
    return seed


def interactive_setup() -> None:
    """Guide the user through Band credential setup.

    Mirrors the Discord / ``_setup_standard_platform`` shape: lazy-imports the
    CLI helpers so the plugin's import surface stays small and prompts for the
    two credentials (plus an optional host override). Invoked by ``hermes
    gateway setup`` with no arguments via the registry ``setup_fn`` hook.

    ACCESS MODEL: there is intentionally no chat-allowlist step. Band's own
    platform ACL is the access gate — a message only reaches the agent if Band
    delivered it (the user could message the agent or add it to a room), so
    ``BandAdapter.enforces_own_access_policy`` is ``True`` and the gateway
    trusts Band traffic without a Hermes-side allowlist or per-user pairing
    codes. ``BAND_ALLOWED_USERS`` / ``BAND_ALLOW_ALL`` remain available as an
    *optional* extra restriction (configured via env / ``hermes config``), not
    a required setup step.
    """
    from hermes_cli.config import get_env_value, save_env_value
    from hermes_cli.cli_output import (
        prompt,
        prompt_yes_no,
        print_header,
        print_info,
        print_success,
        print_warning,
    )

    print_header("Band")

    # Step-by-step credential instructions. Agent creation lives on the Agents
    # page (/agents/new) — NOT Settings, which only holds REST API keys + profile.
    # The agent's API key is shown once in the creation modal.
    print_info("To connect Hermes to Band you need a Band agent's ID and API key:")
    print_info("  1. Open the Band app and go to the Agents page (/agents/new).")
    print_info("  2. Create a new external agent (or open an existing one).")
    print_info("  3. Copy the Agent ID (a UUID) and the agent's API key (shown once).")
    print_info("")

    # Already-configured: offer reconfigure, mirroring Discord / standard setup.
    existing = get_env_value("BAND_AGENT_ID")
    if existing:
        print_success("Band is already configured.")
        if not prompt_yes_no("Reconfigure Band?", False):
            return

    # ── Credentials ──────────────────────────────────────────────────────────
    agent_id = prompt("Band agent ID (UUID)")
    if agent_id:
        save_env_value("BAND_AGENT_ID", agent_id)
        print_success("Saved BAND_AGENT_ID")
    else:
        print_warning("Skipped — Band won't work without an agent ID.")
        return

    # Password=True: masked at the prompt and never echoed back.
    api_key = prompt("Band API key", password=True)
    if not api_key:
        print_warning("Skipped — Band won't work without an API key.")
        return
    save_env_value("BAND_API_KEY", api_key)
    print_success("Saved BAND_API_KEY")

    # Optional base URL (empty → adapter default app.band.ai).
    print_info("")
    print_info("Band host (only override for self-hosted / non-default Band).")
    base_url = prompt("Band base URL (leave empty for app.band.ai)")
    if base_url:
        save_env_value("BAND_BASE_URL", base_url)
        print_success("Saved BAND_BASE_URL")

    # ── Auto-resolved info (no prompts) ───────────────────────────────────────
    print_info("")
    print_info("Access is governed by Band itself — anyone Band lets reach the")
    print_info("agent (in any chat or the hub) can talk to it; no allowlist setup is needed.")
    print_info("Owner, hub room, and home room are resolved automatically:")
    print_info("  • The owner is read from the agent identity on first connect.")
    print_info("  • A private 'Hermes Hub' control room is created automatically")
    print_info("    on first connect and wired as the Band main channel (where")
    print_info("    cron and notification deliveries land).")
    print_info("Band has no DMs — to reach the agent, @mention it in a room (the hub included).")
    print_info("To restrict further, set BAND_ALLOWED_USERS (optional) later.")
    print_info("")
    print_success("🎵 Band configured!")


def register(ctx) -> None:
    """Plugin entry point — called by the Hermes plugin system."""
    ctx.register_platform(
        name="band",
        label="Band",
        adapter_factory=lambda cfg: BandAdapter(cfg),
        check_fn=check_band_requirements,
        validate_config=validate_config,
        is_connected=_is_connected,
        required_env=["BAND_AGENT_ID", "BAND_API_KEY"],
        install_hint="pip install 'band-sdk>=1.0.0,<2.0.0'",
        # Interactive setup wizard — gives Band the same native ``hermes gateway
        # setup`` flow as Slack/Discord (called with no args via this hook).
        setup_fn=interactive_setup,
        env_enablement_fn=_env_enablement,
        # Auth env vars for _is_user_authorized() integration
        allowed_users_env="BAND_ALLOWED_USERS",
        allow_all_env="BAND_ALLOW_ALL",
        # Conservative content cap (no confirmed Band per-message limit).
        max_message_length=BandAdapter.MAX_MESSAGE_LENGTH,
        # Display
        emoji="🎵",
        # LLM guidance
        platform_hint=(
            "You are chatting via Band. Conversations happen in rooms "
            "(not threads); Band has no DMs, so every room is a group room. You "
            "only see messages that @mention you — including in your owner's hub "
            "(control room) — so each turn addressed to you must @mention you. "
            "When you reply, the recipient is @mentioned automatically. Slash "
            "commands are accepted from your owner in any Band room; commands "
            "from anyone else are declined. Keep responses conversational."
        ),
        # Home-channel env var: makes band a valid ``deliver=band`` cron target
        # and lets /sethome (run from a Band room) persist the main channel.
        # The adapter auto-points this at the hub unless explicitly overridden.
        cron_deliver_env_var="BAND_HOME_ROOM",
        # TODO (cron pass): add a standalone_sender_fn for out-of-process
        #   deliver=band cron jobs (gateway runner ref is None there).
    )

    # Register the Band action toolset (Tier-A platform tools + Tier-B
    # room-context tools). Local import so the adapter module imports cleanly
    # even when tools.py's own lazy SDK guard hasn't bound yet. Each tool is
    # async (handlers drive the async REST client) and gated by
    # _check_band_tools_available so the toolset disappears when Band is
    # unconfigured (SDK/creds absent).
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

    # Bundle the guided-setup skill so pip installs ship it. Best-effort: a
    # missing file or an older host without ``register_skill`` must never break
    # plugin load.
    try:
        from pathlib import Path as _SkillPath

        _skill_md = _SkillPath(__file__).parent / "skills" / "add-band" / "SKILL.md"
        if _skill_md.exists():
            ctx.register_skill(
                "add-band",
                _skill_md,
                description="Connect this Hermes agent to Band end-to-end.",
            )
    except AttributeError:
        # Older host without ctx.register_skill — skip the skill silently.
        pass
    except Exception as e:
        logger.debug("[band] Skill registration skipped: %s", e)
