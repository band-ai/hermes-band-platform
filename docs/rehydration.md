# History rehydration — design & decisions

How the Band–Hermes adapter recovers a room's conversation history, and *why* it
works the way it does. Status: shipped (INT-900 / INT-910). Audience: maintainers and
coding agents touching `hermes_band_platform/adapter.py`.

## The problem

Hermes keeps each room's transcript in its own SQLite session store
(`$HERMES_HOME/.../sessions.db`) and rebuilds the LLM prompt from it. When that local
history is gone — a gateway restart with a non-persisted DB, a fresh deploy, or a room
the agent left and rejoined — the agent answered **cold**: it lost context and, worse,
**re-answered questions it had already answered**.

The original fix routed recovered history through `MessageEvent.channel_context`. But
that field is (per the gateway's own docstring in `gateway/platforms/base.py`) a
*require-mention gap-backfill*: a one-shot, role-less string prepended to a single
turn and **never written to the transcript**. So context evaporated after one turn,
and with no role distinction the model couldn't tell its own past replies from user
turns → the re-answer bug.

## The goal

Make **Band the durable source of truth** for history. On a cold room, seed Hermes's
session transcript from Band's server-side history (with correct roles) so it persists
across turns and restarts. Hermes's store becomes a *warm cache* that Band re-seeds at
every cold boundary. We do **not** disable Hermes's store (the gateway has no stateless
mode) — we coexist and control what's in the transcript.

## Architecture (the skeleton)

Everything lives in `hermes_band_platform/adapter.py`. Inbound flow:

```
message_created ─► _handle_message_created
                     _normalize_inbound        → _Inbound (strip self-mention)
                     self-filter / dedup / non-text / owner-command / mention gates
                     _forward_inbound
                       ├─ command?  → handle_message  (skip seed; may inline-await)
                       └─ cold room (flagged in _rehydrate_rooms)?
                            _rehydrate_room ──┬─ _seed_session_from_band  (durable)
                                              └─ _rehydration_context_blob (fallback)
                          handle_message  → gateway builds the prompt from the
                                            now-seeded transcript and answers
```

A room is flagged for one-time rehydration (`_rehydrate_rooms`) at two cold boundaries:

- **Re-join** (`room_added` for a known/left room) — only if `_has_active_session` says
  it's genuinely cold; the re-join also schedules a per-room backlog drain
  (`_schedule_room_catch_up`, see "seed ↔ drain" below).
- **(Re)connect** — `_catch_up_all_rooms` flags any room with no live transcript.

The seed (`_seed_session_from_band`):

1. `get_or_create_session(source)` → `entry.session_id`; if `load_transcript` is
   non-empty the room is warm → stop (idempotent guard).
2. Concurrently (`asyncio.gather`) fetch the **context** (`_fetch_room_context` →
   `get_agent_chat_context`, paginated) and the **answer-set** to exclude
   (`_actionable_answer_ids`).
3. `_build_seed_rows` → text rows with roles (`_seedable_text` + `_seed_role_for`),
   excluding the answer-set.
4. `_atomic_seed_transcript` writes them — atomically, only if still empty.

Catch-up / acks (Route A, the same machinery the gateway lifecycle drives):
`_schedule_catch_up` → `_catch_up_all_rooms` → `_catch_up_room` → `_drain_room`
(pulls `/next`), and `on_processing_start/complete` advance Band's per-message cursor.

## Key decisions (the *why*)

**1. Durable transcript seed, not `channel_context`.** A transcript write survives
turns and restarts; `channel_context` is a single-turn prepend. The blob path is kept
only as a last-resort fallback (see decision 8).

**2. Role-correct seeding kills the re-answer bug.** Our own past replies
(`sender_type=="Agent" && sender_id==self._agent_id`) seed as `role="assistant"`;
everyone else (humans *and peer agents*) as `role="user"`, prefixed `[name]` to match
how the gateway renders a live shared-room message. The transcript then reads as
"already answered," so the model doesn't redo it. (This is the INT-509 class of bug:
dropping own replies leaves the LLM facing unanswered user turns.)

**3. History / backlog boundary — seed context, don't re-answer.** Band's
`get_agent_chat_context` returns *everything*, including messages the agent still owes
an answer. Seeding those *and* answering them = double-answer. So the seed **excludes**
the trigger and the unprocessed **mention** backlog (the messages the live + `/next`
path will answer), while **keeping** un-addressed chatter as context. The exclude-set
comes from `_actionable_answer_ids` — `list_agent_messages(chat_id)` with no status
filter returns everything not yet `processed`; we keep the mentions.

**4. Atomic "seed-if-empty", not a lock.** The gateway runs each turn in an **executor
thread** and persists the transcript *from that thread*, so a concurrent turn-append
can race the seed's check→write. We make the write a single SQLite transaction
(`SELECT COUNT … if 0 INSERT`, via `_atomic_seed_transcript`) — atomic against a
cross-thread append by construction, no check-then-act window, no clobber. An earlier
per-room `asyncio` lock was implemented and then **removed**: it only defended the
asyncio loop (the wrong domain) and became dead weight once the write was atomic.

**5. One notion of "warm" + one key derivation.** "Cold" means *empty transcript*
(`load_transcript`), not merely a missing session entry — `get_or_create_session` can
leave an empty entry that must not count as warm. Cold detection
(`_has_active_session`) and close-on-leave reset (`_reset_room_session`) both derive
the session key via the shared `_session_key_for`, which uses the store's own
`_generate_session_key` — so they target the exact session the gateway uses (correct
even under a multiplexing/profile gateway).

**6. Seed ↔ drain invariant.** The seed excludes the mention backlog *on the assumption
that a drain answers it*. That holds on (re)connect (`_catch_up_all_rooms` drains). A
**live re-join** otherwise only set the flag, so the excluded backlog would be neither
seeded nor answered — so the re-join path now also schedules a per-room drain
(`_schedule_room_catch_up`, no-op while an all-rooms drain is running). *Rule: anywhere
the seed excludes the backlog, a drain must answer it.*

**7. Stable columns only.** Seed rows mirror the gateway's own
`SessionDB.replace_messages` insert (`_MESSAGE_COLUMNS`: `role`, `content`, `tool_*`,
`timestamp`, …). We do **not** depend on `platform_message_id`/`observed` — the gateway
accepts them as kwargs but they aren't in the persisted schema, so they're unsafe
across versions. FTS indexing and `message_count` upkeep come for free because we
mirror the canonical insert (FTS is trigger-based).

**8. Degrade gracefully, fail open.** `_atomic_seed_transcript` returns `None` if the
store can't do an atomic write (no native primitive *and* no usable `_db`); seeding
errors are swallowed. Either way the caller falls back to the `channel_context` blob
(`_rehydration_context_blob`) so the message is still delivered with *some* recovered
context — never blocked.

## Hermes integration & constraints

- **Plugin contract.** Hermes loads the *package* via the `hermes_agent.plugins` entry
  point and calls `register(ctx)`, which hands it `adapter_factory=lambda cfg:
  BandAdapter(cfg)`. Internal module layout is invisible to the gateway — only
  `register` and the single `BandAdapter` class are the contract.
- **What we rely on.** Public `SessionStore`: `get_or_create_session`,
  `load_transcript`, and the gateway-native `seed_transcript_if_empty` *when present*.
  When it's absent we use `store._db._execute_write` + the documented `messages` schema
  directly (the one deliberate coupling to two stable internals that
  `replace_messages` also uses). Capability-guarded throughout.
- **Validated execution model.** The agent turn runs via
  `_run_in_executor_with_context(run_sync)` (`gateway/run.py`) and writes the transcript
  from that worker thread; `SessionDB._execute_write` serialises writes with
  `threading.Lock` + `BEGIN IMMEDIATE`. This is *why* decision 4 is correct and a
  loop-level guard is not.
- **Reset policy.** A room's session reset policy should be `none` so an idle/daily
  timer doesn't reset mid-conversation and force a needless re-seed; Band re-join
  semantics drive resets instead.

## Validated against real artifacts

Confirmed against `hermes-agent 0.17.0`, `hermes_state`, and `band-client-rest 0.0.10`
(not guesses): the executor-thread transcript writes; the `messages` schema + role
values (`user`/`assistant`) accepted by replay; `list_agent_messages` (no status) =
not-`processed`, cursor-paginated; key parity via `_generate_session_key`; the
shared-room `[name] text` render; and — directly reproduced — the clobber on the old
load→rewrite path, *eliminated* by the atomic write (40-session thread-contention
stress, zero clobbers/corruption). Tests live in `tests/test_adapter.py`
(`TestAtomicSeedTranscript`, `TestDurableSeedRehydration`, `TestSessionKeyParity`, …).

## Lesson for future changes (read before editing the seed/store paths)

The cross-thread race was initially missed because **every review checked the gateway's
*data contract* (signatures, schema, key derivation) but never its *execution /
concurrency model*** — which thread writes shared state, and the domain in which
atomicity must hold. "No `await` between ⇒ atomic" is true on one loop and a silent
trap once a second thread shares the resource.

So, **run the execution-model gate** whenever a change matches this signature:

- a **check-then-act** on shared state (`load_* … then write_*`, count-then-insert);
- a write to **state the gateway also owns** (`_session_store`, the link, anything set
  via `set_*`);
- an `async` callback **invoked by the gateway** (may fire on a non-adapter loop — the
  INT-899 lesson behind `_send_on_link`'s cross-loop marshalling);
- a claim of atomicity justified by "no `await` between".

For each: name every writer and its thread, and state the serialization domain
(asyncio loop vs SQLite transaction vs OS lock). Prefer the platform's own primitives
(`BEGIN IMMEDIATE`, the compression advisory lock) over bespoke loop guards.

## Follow-ups (non-blocking)

- **Upstream `seed_transcript_if_empty`.** The atomic write currently ships as an
  in-plugin helper using `store._db`. A gateway-native `seed_transcript_if_empty`
  (PR to NousResearch) would let the `_db` path retire — the adapter already prefers
  the native method when present.
- **`sessions.db` persistence.** If `$HERMES_HOME/runtime/` isn't on a mounted volume,
  a restart wipes the DB. Durable seeding *masks* this (cold → re-seed), but the volume
  should be mounted so mid-life history isn't needlessly refetched.
