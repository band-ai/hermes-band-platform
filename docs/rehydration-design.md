# Band-managed history rehydration — design & implementation plan

Status: proposed · Branch: `claude/band-hermes-rehydration-6tkrad` · Team: Integration

## 1. Problem

After a gateway restart or a room re-join, the Band–Hermes plugin does not reliably
restore conversation history. Agents lose context, and in some cases **re-answer
questions they already answered** (the duplicate-response class of bug). Reported in
INT-900.

### Root cause

Rehydration is delivered through `MessageEvent.channel_context`. Per the public
gateway (`gateway/platforms/base.py`), that field is:

> *"Channel context recovered by history backfill (e.g. messages between bot turns
> that were missed due to require_mention) … prepend this context afterward."*

So `channel_context` is a **one-shot, text-only, role-less** prepend for a *single*
turn — designed for require-mention gap backfill, **not** cold-start recovery. It is
never written to the gateway session transcript (`sessions.db`), so it evaporates
after one turn. Two further defects compound it:

- `_fetch_rehydration_context` (`adapter.py:1356`) flattens history to `who: text`
  lines with **no role distinction**, so the model cannot tell its own past replies
  from user turns → it re-answers already-answered questions.
- The re-join flag path (`adapter.py:905`) sets the rehydrate flag **unconditionally**,
  without the active-session guard the restart path has (`adapter.py:1269`), so a
  healthy room can get stale context re-injected on a spurious `room_added`.

## 2. Goal & non-goals

**Goal.** Make Band the *durable source of truth* for conversation history. On a cold
room, seed the gateway session transcript from Band's server-side history with correct
roles, so context survives subsequent turns and restarts.

**Non-goals.**
- Disabling Hermes's session store. The public gateway has no stateless mode; we
  *coexist* and control transcript contents instead (verified against
  `gateway/session.py`).
- Per-turn mirroring of Band into the transcript (expensive, fights the gateway).
- Fixing `sessions.db` volume persistence — related but separate (see §10).

## 3. Design overview

Hermes's SQLite session store becomes a **warm cache**; Band re-seeds it as the source
of truth at every cold boundary (restart, re-join, wiped DB). We depend only on the
**public, documented** `SessionStore` surface:

| Method | Use |
|---|---|
| `get_or_create_session(source) -> SessionEntry` | resolve/create the session, get `.session_id` |
| `load_transcript(session_id) -> list[row]` | public cold-room check (transcript empty?) |
| `append_to_transcript(session_id, message)` | seed rows (stable keys only) |

Stable transcript-row keys (from the documented schema): `role`, `content`,
`tool_name`, `tool_calls`, `tool_call_id`, `timestamp`. We **do not** rely on
`platform_message_id` or `observed` — `session.py` passes them but they are **not in
the persisted schema**, so they are unsafe across gateway versions.

## 4. How it works (the flow)

A single seeding path serves both cold entrypoints (re-join and restart), because both
already funnel into `_handle_message_created` via the `_rehydrate_rooms` flag.

```
cold room detected ──► flag in _rehydrate_rooms
                           │
   first trigger message arrives (passes mention/command gate)
                           │
                  ┌────────▼─────────────────────────────────┐
                  │ _seed_session_from_band(room_id, exclude) │
                  │  1. entry = store.get_or_create_session   │
                  │  2. if load_transcript(entry.id): return  │  ← idempotent guard
                  │  3. ctx = get_agent_chat_context(room)    │
                  │  4. for item in ctx, id ∉ exclude:        │
                  │        append_to_transcript(role,content) │
                  └────────┬─────────────────────────────────┘
                           │
            self.handle_message(trigger)  ── gateway loads the seeded
                                             transcript, appends the trigger,
                                             builds the prompt, answers
```

The seed runs **before** `handle_message`, so the gateway builds the prompt from a
populated session. Seeding once into an empty transcript makes it safe across repeated
restarts even though `append_to_transcript` is non-idempotent (append-only, no UNIQUE).

### The history/backlog boundary (the one tricky rule)

Band exposes per-message delivery status (`pending/processing/processed/failed`) via
`agent_api_messages.list_agent_messages(chat_id, status=…)`, and the `/next` actionable
set "excludes only `processed`" (`link.py:653`). That gives a platform-computed split:

- **Seed as history** = Band context **except** the actionable (not-yet-`processed`)
  mention backlog.
- **Answer as triggers** = the actionable backlog, via the existing Route A `/next`
  catch-up + the live trigger.

So `exclude` = the actionable backlog ids for the room at seed time (plus the current
trigger id). Seeded rows and answered turns are then **disjoint by construction** — no
message is both seeded and answered.

## 5. Pitfalls to address

| # | Pitfall | Mitigation |
|---|---------|-----------|
| P1 | **Re-answering answered questions** (INT-509 class). Dropping the agent's own replies leaves the LLM facing unanswered user turns. | Map Band `sender_type=="Agent" && sender_id==self._agent_id` → `role="assistant"`; everyone else → `role="user"`. Own replies seed as `assistant` so the transcript reads "already answered". |
| P2 | **Trigger / backlog double-answer.** The trigger and offline backlog also appear in `get_agent_chat_context`; seeding them *and* answering them duplicates. | Exclude the actionable backlog + current trigger id from the seed (the §4 boundary). |
| P3 | **Double rows** (`append` is non-idempotent, no UNIQUE on `platform_message_id`). | Seed once, guarded by `load_transcript(...)` being empty. Never seed a non-empty session. |
| P4 | **Spurious re-join rehydration.** `room_added` for an already-known healthy room is misread as a re-join (`adapter.py:905`). | Add the `not _has_active_session(...)`/empty-transcript guard to the re-join path so only genuinely cold rooms seed. |
| P5 | **Fragile cold detection.** `_has_active_session` peeks gateway privates (`session_store._entries`, `_ensure_loaded()`). | Replace with public `load_transcript(session_id)` emptiness check. |
| P6 | **Lost mid-turn context.** Status-only (`processed`) seeding would drop un-addressed chatter. | Seed from `get_agent_chat_context` (full conversation) and *subtract* the backlog, rather than seeding `processed`-only. |
| P7 | **Speaker attribution** in multi-party rooms. | Prefix `user`-role content with `[sender_name]: ` (consistent with how the gateway persists group turns — verify §9). Own `assistant` rows stay bare. |
| P8 | **Mentions & ordering.** Band content carries `@[[uuid]]` mentions; rows must be chronological. | Reuse `replace_uuid_mentions(...)`; append in `inserted_at` order with `timestamp` set. |
| P9 | **Gateway version skew.** `SessionStore` methods could change; `platform_message_id`/`observed` already diverge from the schema. | `hasattr`-guard the seed; **fall back to the existing `channel_context` path** if the API is absent. Never touch undocumented columns. |
| P10 | **Mid-conversation auto-reset** re-seeds needlessly. | Document recommending the room's session reset policy = `none` (Band re-join semantics drive resets, not idle timers). |

## 6. Code changes (`hermes_band_platform/adapter.py`)

**Add**
- `_seed_session_from_band(room_id, *, exclude_ids) -> bool` — the durable seed (§4).
  Returns whether it seeded (for logging/metrics).
- `_actionable_backlog_ids(room_id) -> set[str]` — the not-`processed` ids for the
  boundary (via `list_agent_messages`; see §9).
- `_session_id_for(room_id) -> str | None` — `get_or_create_session` + return
  `.session_id`, building the same `SessionSource(platform, chat_id, _SESSION_CHAT_TYPE)`
  used elsewhere.
- A small role-mapping helper (own → `assistant`, else → `user`) and a typed
  transcript-row builder.

**Modify**
- `_handle_message_created` (`~:1136`): replace the `channel_context =
  _fetch_rehydration_context(...)` block with `_seed_session_from_band(...)` (compute
  `exclude_ids = {trigger_id} | _actionable_backlog_ids(room_id)`), then call
  `handle_message` **without** `channel_context` (keep it only on the P9 fallback).
- Re-join path (`~:905`): seed only when the room is genuinely cold (guard P4/P5).
- `_catch_up_all_rooms` (`~:1269`): keep cold flagging but route through the public
  emptiness check.

**Remove / retire**
- `_fetch_rehydration_context` (`:1356`) and the `channel_context` injection on the
  primary path. Keep a thin text-blob fallback only under the P9 capability guard.
- `_has_active_session`'s private-state peeking (`:1499`) → public `load_transcript`.

**Cold-detection helper sketch** (illustrative):

```python
def _session_id_for(self, room_id: str) -> str | None:
    store = getattr(self, "_session_store", None)
    if store is None or not hasattr(store, "get_or_create_session"):
        return None
    source = SessionSource(
        platform=self.platform,
        chat_id=room_id,
        chat_type=_SESSION_CHAT_TYPE,
    )
    return store.get_or_create_session(source).session_id
```

## 7. Modern, clean, readable Python

- `from __future__ import annotations`; full type hints; `str | None`, `list[str]`,
  `set[str]` — no `Optional`/`List`.
- A `TypedDict` (`TranscriptRow`) for the `append_to_transcript` payload so the stable
  key set is explicit and type-checked — no loose `dict[str, Any]` at call sites.
- Small, single-responsibility methods (resolve session id · compute boundary · map a
  row · seed). No method does two jobs.
- Pure helpers (role mapping, row building) kept free of I/O so they unit-test without
  mocks.
- Guard clauses over nested `if`; early `return` on the not-cold / no-store paths.
- **Public APIs only** — no reaching into `_entries`/`_ensure_loaded()`.
- `logging` with `%s` lazy args; no `print`. Docstrings explain *why* (the boundary
  rule, the idempotency guard), not the obvious.
- `pydantic.ValidationError` caught separately from generic `Exception`; seeding is
  best-effort and must never block a live message.
- Passes `ruff check`, `ruff format`, `pyrefly check` (the band-sdk standards this repo
  mirrors).

## 8. Testing plan (`tests/test_adapter.py`)

Update the existing rehydration tests (they currently assert `channel_context`) to the
seed model, and add coverage per pitfall:

- **P1**: cold room with prior own replies → seeded rows include `assistant` turns;
  the trigger is answered once, no re-answer of older questions.
- **P2/P6**: `get_agent_chat_context` returns history + actionable backlog → seeded set
  excludes backlog ids; backlog still answered via the drain.
- **P3**: second message in a now-warm room → no re-seed (`load_transcript` non-empty).
- **P4**: `room_added` for a warm room → **no** seed; for a genuinely cold/left room →
  seed.
- **P5**: cold detection uses `load_transcript`, not `_entries`.
- **P9**: store without `get_or_create_session` → falls back to `channel_context`,
  message still delivered.
- Role mapping + row builder: pure unit tests, no mocks.

Run: `uv run pytest tests/ -v` and the `ruff`/`pyrefly` pre-commit checks.

## 9. Verification — RESOLVED

Verified against the real gateway (`hermes-agent 0.17.0`, PyPI) and REST client
(`band-client-rest 0.0.10`).

1. **Backlog enumeration → one call.** `list_agent_messages(chat_id)` with **no
   `status`** returns "everything NOT processed" (chronological, cursor-paginated) — the
   trigger + offline backlog. We keep the mentions (only those are answered). No
   per-status enumeration needed; the context endpoint carries no per-item status, so
   this is the boundary source.
2. **Role enum.** `get_messages_as_conversation()` returns OpenAI `user`/`assistant`;
   the gateway also writes `system`/`session_meta`/tool rows. Text seeding uses
   `user`/`assistant` — confirmed accepted by the replay path.
3. **Group-turn shape.** For a shared multi-user session the gateway renders a live
   peer message as `"[{user_name}] {text}"` (`run.py`; space, **no colon**). Band group
   rooms are shared (`is_shared_multi_user_session` → True), so seeded peer rows mirror
   `"[name] content"`; own rows stay bare `assistant` content.
4. **Session-key parity — guaranteed.** `get_or_create_session(source)` →
   `_generate_session_key` → `build_session_key`. Passing the **same `source`** object
   the gateway uses for the live message yields the identical key (we no longer
   hand-rebuild it), so the seeded session is exactly the one the trigger lands in.

Transcript-row schema confirmed: stable columns are `role`, `content`, `tool_*`,
`timestamp`; `platform_message_id`/`observed` are accepted by `append_to_transcript`
but **not persisted columns**, so the implementation never depends on them.

## 10. Out of scope / related

- **`sessions.db` persistence.** The actual reboot trigger may be that
  `$HERMES_HOME/runtime/sessions.db` isn't on a mounted volume. Durable seeding *masks*
  this (a wiped DB just re-seeds from Band), but the volume should still be fixed so
  mid-life history isn't needlessly refetched. Track separately.

## 11. Rollout

- Behind the P9 capability guard, so an older gateway degrades to today's behaviour
  rather than breaking.
- Land with the bug fixes (P4/P5) even if the full seed is staged, since they are
  self-contained correctness fixes.
