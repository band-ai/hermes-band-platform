# Federated LLM wiki query between friends — design

Status: approved by Mr. Ofer, pending implementation plan.

## Motivation

Hermes agents each maintain a local "LLM wiki" (Hermes core's bundled
`research-llm-wiki` skill — out of scope here, already works today). The goal
is to let a Hermes agent:

1. Connect to other Hermes agents ("friends") over Band — send a connection
   request, have the other side approve it.
2. Ask a question and get one answer synthesized from its own wiki plus every
   connected friend's wiki, without the user having to manually poll for
   replies.

A prior implementation of this existed in `band-hermes` (the predecessor to
this plugin), built on a hand-rolled `httpx` REST client with its own contact
management (`band_connect` / `band_list_friends` / `band_respond_request`) and
an `/ask_wikis` slash command that broadcast a question into a shared "wiki
round-table" room and told the user to manually check `/band_inbox` later —
because that architecture had no live push; a friend's agent only "read" new
messages when its own user prompted it to check.

This plugin (`hermes-band-platform`) is a full rewrite on `band-sdk` 1.0.0
with a genuinely different property: `BandAdapter._consume()` proactively
wakes the Hermes agent loop (`self.handle_message(...)`) the moment an
`@mention` arrives, without any user polling. That live-push property is what
makes real (not manual-check) federation possible, and is the basis for this
design.

Two further facts drove the design:

- `band-sdk` 1.0.0 already ships a **complete native contacts subsystem**
  (`agent_api_contacts` REST endpoints, a `ContactTools` helper class, and
  streaming `contact_request_received` / `contact_request_updated` /
  `contact_added` / `contact_removed` events) that this plugin does not use
  at all yet.
- `adapter.py:1144` already carries the marker
  `# TODO (contacts pass): handle contact_* events. Ignored this release.`
  — i.e. this exact gap was anticipated and deliberately deferred by the
  plugin's own authors.

So this design is largely about wiring up existing `band-sdk` capability
into this plugin's existing patterns, plus one new piece of original logic:
a deterministic state machine that tracks a federated query across multiple
asynchronous replies and a timeout, then triggers one synthesized answer.

## Part 1 — Connections (contacts)

Reuse `band-sdk`'s native contacts system rather than hand-rolling a second
one (as the old repo did, before this SDK feature existed).

### New tools (`hermes_band_platform/contacts.py`, new file)

Thin wrappers around `band.runtime.contact_tools.ContactTools` (already a
`band-sdk` class — it wraps `rest.agent_api_contacts.*` and returns plain
dicts), following `tools.py`'s existing conventions (`_rest()` to get the
live link or an env-creds fallback client, `_tool_exc()` for error shaping,
`tool_result`/`tool_error` for the JSON envelope):

- `band_add_contact(handle, message=None)` — send a connection request to
  another Hermes agent's Band handle.
- `band_list_contacts()` — list current approved contacts.
- `band_list_contact_requests()` — list pending received/sent requests.
- `band_respond_contact_request(action, request_id)` — approve / reject /
  cancel.

### Adapter changes (`adapter.py`)

1. In `connect()`, alongside the existing
   `await self._link.subscribe_agent_rooms(self._cfg_agent_id)`
   (`adapter.py:618`), add:
   `await self._link.subscribe_agent_contacts(self._agent_id)`.
   This is an existing `BandLink` method (`band/platform/link.py:250-265`)
   that subscribes to a WebSocket channel whose four event types push onto
   the same internal queue `_consume()` already reads via
   `async for event in self._link`. It is not new SDK surface — the plugin
   simply never asked for these events before.
2. In `_handle_event()`, replace the `# TODO (contacts pass)` marker
   (`adapter.py:1144`) with four branches: `contact_request_received`,
   `contact_request_updated`, `contact_added`, `contact_removed`. Each
   formats a human-readable line, e.g.:
   `[Contact Request] Alice (@alice/hermes) wants to connect. Request ID: abc-123`
   and delivers it via
   `self.handle_message(MessageEvent(text=..., source=self.build_source(chat_id=self._hub_room_id, chat_type=_SESSION_CHAT_TYPE), internal=True, raw_message=payload))`
   — the identical synthetic-injection idiom `_handle_participant_change`
   (`adapter.py:1169-1204`) already uses for "X joined this room" notices.
   No new room type, no new delivery mechanism.
3. If the Hub is not yet bootstrapped (`self._hub_room_id` is `None`),
   contact events are logged and dropped rather than queued — mirrors the
   existing fail-closed posture the Hub already has for slash commands when
   the owner is unresolved.

### New skill: `band:contacts`

Teaches the Hub-resident LLM: when a `[Contact Request]` line appears, tell
the owner who's asking and why, and wait for their instruction before calling
`band_respond_contact_request` (unless the owner has stated a standing
auto-approve preference in that conversation already).

## Part 2 — Federated wiki query

### Flow

```
Owner: "ask all my wikis about X"
  -> agent searches its own wiki locally (Hermes' bundled research-llm-wiki
     skill — already works, no plugin changes needed)
  -> band_ask_wikis(query="X", local_findings=<own hits, or omitted>)
       -> resolves target friends: default = every contact with type "Agent"
          (band_list_contacts already returns type); an explicit `friends`
          arg narrows this to named contacts
       -> creates a FRESH room (not reused across queries -- see "Why a
          fresh room" below), adds each friend, posts the query mentioning
          them all
       -> records a _PendingFederation in the adapter, returns immediately:
          "Asked 3 friends, I'll let you know within 5 minutes"

Each friend's own live Hermes agent (a separate gateway/process):
  -> wakes on the @mention (existing live-push behavior, unchanged)
  -> guided by the federated-wiki-search skill: searches its own wiki,
     replies in the room, explicitly passing `mention_ids` that include
     the asking agent's participant id (the tool's default mention
     fallback -- "all non-agent participants" -- would otherwise exclude
     the asking agent, since it's an Agent-type participant)

Requester's adapter, in the background:
  -> `_handle_message_created` recognizes the room as a pending federation
     (checked before normal turn dispatch) and routes to a dedicated
     handler: records the reply, does NOT start a normal LLM turn in that
     room (so the requester's agent doesn't chatter back into the
     round-table after every reply)
  -> once every expected friend has replied, OR 5 minutes elapse
     (whichever first): finalize -- inject one synthetic turn into the
     ORIGINAL requester source (wherever `band_ask_wikis` was called from:
     the Hub, another Band room, or a non-Band session falling back to the
     Hub/home room per the existing `_resolve_room_for_send` pattern),
     containing the own-wiki findings plus every reply (or "(no reply,
     timed out)" for stragglers), asking the LLM for ONE consolidated
     answer.
```

### Why a fresh room per query (not a persistent reused "wiki round-table")

The old repo reused one persistent room per matching friend-set so replies
would land in one browsable thread, because the user had to manually scroll
through it. Here, replies are collected and synthesized automatically, so
that browsability benefit no longer applies. Reusing a room would also
require correlating multiple concurrent queries within the same room (Band
has no threads), which the deterministic state machine avoids entirely by
keying `_pending_federations` on `room_id` -- one room, one in-flight query.

### New tool (`hermes_band_platform/federation.py`, new file)

`band_ask_wikis(query, friends=None, local_findings=None)` — resolves the
live adapter/link the same way `tools.py`'s `_rest()` does, creates the room
and participants via the same REST calls `tools.py`'s `_handle_create_room`
already uses, and registers the pending state on the live `BandAdapter`
instance.

### New adapter state (`adapter.py`)

```python
self._pending_federations: Dict[str, _PendingFederation]  # keyed by room_id
```

alongside the existing `_participants_cache` / `_last_human_sender` dicts —
same idiom: in-memory, resets on gateway restart (see Limitations).

`_PendingFederation` (dataclass, in `federation.py` so it's importable and
unit-testable without a live link):

- `query: str`
- `local_findings: Optional[str]`
- `requester_source: Any` — the session/platform source to deliver the
  final answer to (captured from the calling tool invocation's session
  context, falling back to the Hub exactly like `_resolve_room_for_send`).
- `expected_agent_ids: set[str]`
- `replies: dict[str, tuple[str, str]]` — `agent_id -> (name, content)`
- `timeout_task: asyncio.Task` — scheduled the same way
  `_schedule_catch_up` / `_schedule_room_catch_up` already schedule
  background tasks.

### Adapter hook

In `_handle_message_created`, before the existing mention-gate/dispatch
logic, add: `if inb.room_id in self._pending_federations: return await self._handle_federation_reply(inb)`.
`_handle_federation_reply` records the reply if `inb.sender_id` is in
`expected_agent_ids` and not already recorded; if that completes the
expected set, cancels the timeout task and finalizes immediately.

### Finalization

Format a digest, e.g.:

```
Your question "X" got 2/3 replies:
- Alice: <reply text>
- Bob: <reply text>
- Carol: (no reply, timed out)

Your own wiki: <local_findings, if any>

Summarize this for the user.
```

and inject it via `self.handle_message(...)` targeting
`requester_source` — the same synthetic-injection idiom used in Part 1 and
in the existing `_handle_participant_change`. Remove the entry from
`_pending_federations` after finalizing.

### Counterpart skill: `band:federated-wiki-search`

Adapted from the old repo's skill of the same name. Covers both roles:

- **Asking**: search the local wiki first (via `research-llm-wiki`), then
  call `band_ask_wikis` with the local hits included.
- **Answering**: when mentioned in a room that looks like a federation
  query, search the local wiki and reply, explicitly passing `mention_ids`
  covering every other room participant (not relying on `band_send_message`'s
  default mention fallback, which excludes agent participants).

## Part 3 — Files, registration, testing

| File | Change |
|---|---|
| `hermes_band_platform/contacts.py` | New — contact tools (Part 1). |
| `hermes_band_platform/federation.py` | New — `band_ask_wikis`, `_PendingFederation`, digest formatting (Part 2). |
| `hermes_band_platform/adapter.py` | `subscribe_agent_contacts` in `connect()`; 4 new `contact_*` branches in `_handle_event()` replacing the TODO; `_pending_federations` state + early-intercept in `_handle_message_created`; timeout scheduling. |
| `hermes_band_platform/__init__.py` | Register the 5 new tools and 2 new skills, same loop/pattern as the existing `BAND_TOOLS` registration. |
| `hermes_band_platform/skills/band-contacts/SKILL.md` | New. |
| `hermes_band_platform/skills/federated-wiki-search/SKILL.md` | New (adapted from the old repo). |
| `hermes_band_platform/plugin.yaml` | No new env vars — no new credentials are needed for this feature. |

### Testing (pytest + pytest-asyncio, matching existing `tests/` style)

- `contacts.py` handlers: monkeypatched REST client, assert `tool_result`/
  `tool_error` JSON shape — same pattern as existing `tests/test_tools.py`.
- Contact-event formatting: pure function, direct unit tests (same style as
  existing tests for `_seedable_text`, `_is_low_signal_ack`, etc.).
- `_PendingFederation` state machine: fake adapter/link, simulate replies
  arriving to assert early completion, and simulate no replies to assert the
  timeout path produces the right digest. The timeout is tested by calling
  the finalize method directly, not by sleeping 5 real minutes.
- `register(ctx)` contract test: assert the new tool/skill counts using the
  existing `FakeCtx` pattern (mirrors `tests/test_adapter.py`'s existing
  coverage of `register()`).

### Error handling / edge cases

- A contact whose type is not `"Agent"` (a human) is excluded from
  `band_ask_wikis`'s default fan-out (it can't answer a wiki query); an
  explicit `friends` argument naming them still surfaces a clear warning
  rather than silently dropping them.
- Zero agent-type contacts → `band_ask_wikis` fails fast with a clear error
  ("no agent contacts yet — use band_add_contact"); no room is created.
- A friend agent that never replies (offline, crashed, etc.) is covered by
  the 5-minute timeout and reported as "(no reply)" in the digest — the
  query is never left open indefinitely.
- Gateway restart mid-query: `_pending_federations` is in-memory only, so an
  in-flight query and any late replies are orphaned across a restart. This
  is a known, documented limitation, consistent with how this file already
  documents other in-memory-only state (`_last_sender_by_room`-equivalents,
  `_seen_inbound_ids`).

## Out of scope

- Any change to Hermes core's `research-llm-wiki` skill — it already works
  and is invoked by the LLM naturally; this feature only ensures its
  findings get folded into the federated answer.
- Persisting `_pending_federations` across restarts.
- A configurable timeout value — fixed at 5 minutes per Mr. Ofer's decision,
  favoring simplicity (KISS) over a config knob nobody asked for yet.
- Removing a contact (`band_remove_contact`) — not required by the stated
  use case; can be added later following the same pattern if needed.
