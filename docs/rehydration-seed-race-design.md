# Seed-if-empty race — detailed design + retrospective

Status: **resolved (shipped)** · Follows: `docs/rehydration-design.md` §10/§12 · Issue: INT-910 follow-up

Every claim below is validated against the shipped Hermes source
(`hermes-agent 0.17.0`, `hermes_state`) — file/method citations inline. "Hermes
docs" here means those modules' docstrings + the schema, which are the
authoritative spec for the gateway's behaviour.

---

## 0. Resolution (as shipped)

The race is closed by an **atomic, single-transaction "seed only if empty"**, and
the earlier interim mitigation was **removed** as redundant once the atomic write
landed — a net reduction in concurrency surface:

- **Added** `_atomic_seed_transcript(store, session_id, rows)` (in `adapter.py`):
  prefers a gateway-native `seed_transcript_if_empty`, else drives
  `SessionDB._execute_write` directly — one `BEGIN IMMEDIATE` doing
  `SELECT COUNT` then the inserts (mirroring `replace_messages`' columns,
  `_encode_content`, and `message_count` upkeep; FTS via triggers; the COUNT uses
  the raw `conn` to avoid `_db`'s non-reentrant-lock deadlock). `None` ⇒ the store
  can't write atomically ⇒ caller falls back to the one-shot `channel_context` blob.
- **Removed** the per-room `asyncio` seed lock (`_seed_locks` / `_seed_lock_for`),
  the command-vs-non-command gating in `_handle_message_created`, the best-effort
  `load_transcript`-recheck → `rewrite_transcript` tier, and the W0b telemetry.
- **Validated** under real thread contention against the real `SessionDB`: a
  concurrent `append` vs `seed_if_empty` never clobbers the append and never
  corrupts the transcript (40-session stress test; deterministic empty/no-op
  cases; special-char round-trip).

The capability ladder is now two tiers: **atomic write** (native or via `_db`) →
**`channel_context` blob**. The §5 "Phase C lock" / "Phase B upstream" plan below
is retained as the design rationale; what shipped is the Phase-B write done as an
in-plugin helper (no monkeypatching — an explicit function using `store._db`),
which also makes the lock unnecessary.

---

## 1. The defect, precisely (validated)

The cold-room seed is a check-then-act:

```
load_transcript(session_id)  == empty   # check   (one SessionDB transaction)
rewrite_transcript(session_id, rows)     # act     (another SessionDB transaction)
```

**Why "no await between" does NOT make this safe.** I originally closed this by
putting no `await` between the re-check and the write, reasoning it was atomic.
That defends only the *adapter's asyncio loop*. The gateway writes the transcript
from a **different OS thread**:

- The agent turn runs in an executor thread: `agent_result = await self._run_agent(...)`
  (`run.py:9406`) → `run_sync()` → `await self._run_in_executor_with_context(run_sync)`
  (`run.py:11078`), and `run_sync` calls `agent.run_conversation(...)` with
  `session_db=self._session_db` passed in (`run.py:11068`).
- The agent persists transcript rows **from that worker thread** during the turn —
  the post-turn code skips re-writing precisely because "the agent already persisted
  these messages to SQLite via `_flush_messages_to_session_db()`" (`run.py:9730`,
  `agent_persisted = self._session_db is not None`).

`SessionDB` is built for this: every write goes through `_execute_write`, which holds
a `threading.Lock`, runs `BEGIN IMMEDIATE`, and retries on `database is locked`
(`hermes_state.SessionDB._execute_write`). The published docs say the same:
*"SQLite … WAL mode for concurrent readers and a single writer, which suits the
gateway's multi-platform architecture … BEGIN IMMEDIATE transactions … application-level
retry with random jitter"* (`website/docs/developer-guide/session-storage.md`). So
**individual** ops are atomic and thread-safe — but our *check* and *act* are two
separate `_execute_write` transactions, so a worker-thread agent-flush can commit in
the gap.

**Reachability (two adapter task lanes).** Exactly one message consumes the
`_rehydrate_rooms` flag and seeds; its own turn is dispatched only *after* the seed.
The collision is across the adapter's two concurrent tasks:

1. the live consumer (`_consume`), and
2. the catch-up drain (`_schedule_room_catch_up` / `_catch_up_all_rooms`).

Sequence: a drained message begins seeding (awaiting its two fetches); meanwhile a
**live** message for the *same cold room* arrives on the consumer lane, finds the flag
already gone, and is dispatched → its turn starts in an executor thread and flushes
rows. That flush races the seed's check→write.

**Severity (bounded, self-healing).** Worst case: the seed's `rewrite_transcript`
(delete-all + insert) clobbers the one concurrently-flushed turn's rows, or the seed
is skipped. The user still got the reply; only *history* loses ≤ one turn's rows,
once, and Band re-seeds on the next cold boundary. Mitigated further by
`SessionDB._is_duplicate_replayed_user_message` (the DB already dedups replayed user
rows) and first-class empty/ghost-session cleanup
(`delete_session_if_empty`, `prune_empty_ghost_sessions`).

**Conclusion:** real, cross-thread, low-severity, self-healing.

---

## 2. Goal & constraints

- **Atomic "seed only if the transcript is empty"** — no TOCTOU against concurrent
  turn-appends.
- **Public Hermes only** (no fork): either reuse an existing primitive or add a small,
  clearly-correct one upstream.
- **Graceful degradation**: older gateways without the primitive must still work
  (fall back to today's best-effort seed, then to the `channel_context` blob).
- **No throughput regression / deadlock.**

---

## 3. The Hermes-blessed pattern (validated)

Hermes already faces the identical hazard — compression is a transcript
read-modify-write (read tip → rewrite lineage) — and solves it two ways:

1. **Single-transaction conditional writes.** `try_acquire_compression_lock` is
   documented as *"single-transaction DELETE-expired + INSERT-or-IGNORE, followed by a
   SELECT to confirm we got the row. SQLite serialises writes, so the whole sequence is
   atomic against other writers."* That is exactly the construction we need: do the
   check and the conditional write **inside one `BEGIN IMMEDIATE` transaction**.
2. **An advisory session lock** around the larger rotation
   (`try_acquire_compression_lock` / `release_compression_lock`, with expired-lock
   reclaim so a crashed holder can't block forever).

So the canonical fix is **not** a loop-level guard — it's a single
`_execute_write` transaction that checks emptiness and writes within it.

---

## 4. Options (each validated against source)

| Option | Verdict | Why (validated) |
|---|---|---|
| **A. Reuse the compression lock** for the seed | ❌ Reject | The lock only serialises *compressors*. A normal turn-append goes through `append_message` → `_execute_write` and does **not** take the compression lock, so holding it wouldn't serialise the seed against the actual concurrent writer. |
| **B. Upstream `SessionDB.seed_transcript_if_empty(session_id, rows)`** — one `BEGIN IMMEDIATE` txn: `SELECT COUNT(*)`; if 0, insert rows + set `message_count` | ✅ **Correct fix** | Atomic against `append_message` (both are `_execute_write`/`BEGIN IMMEDIATE`). Direct analogue of `try_acquire_compression_lock`'s construction. Small, idiomatic, low-risk to upstream. |
| **C. Adapter-only per-room `asyncio.Lock`** (serialise seed vs the adapter's own dispatch) | ◑ Interim only | Shrinks the window (no new turn *starts* during a seed) but cannot recall a turn already running in the executor, and never crosses into the gateway's thread. Window-shrink, not elimination. No upstream dep. |
| **D. Accept + instrument** | ✅ First step | Measure real-world frequency before investing; zero risk. |

---

## 5. Chosen design

Phased: **D → C → B**. Each phase is shippable on its own and strictly improves on the
last; B is the terminal, correct state.

### 5.1 Phase D — instrument (now)

In `_seed_session_from_band`, count two events: (a) the re-check found the transcript
non-empty (the race fired, harmlessly), and (b) a seed was skipped. Emit a one-line
`logger.info` (or a metric if the gateway exposes one) with the room id. Ship, observe.
This both quantifies the race and validates the threading assumption empirically.

### 5.2 Phase C — adapter interim mitigation (if D shows it fires)

A per-room `asyncio.Lock` (`self._seed_locks: dict[str, asyncio.Lock]`), acquired:

- around the flag-consume + seed in `_handle_message_created`, and
- before dispatching any message for that room to the gateway.

Effect: the seed and the adapter's own dispatches for a room are mutually exclusive, so
no *new* turn starts mid-seed. Residual (documented): a turn dispatched just before the
seed can still flush from its executor thread during the seed. Keep the existing
empty re-check. This is explicitly a window-shrink, not a fix.

### 5.3 Phase B — upstream atomic primitive (terminal)

**`hermes_state` (`SessionDB`):**

```python
def seed_transcript_if_empty(self, session_id: str, messages: list[dict]) -> bool:
    """Insert `messages` iff the session currently has zero rows. One BEGIN
    IMMEDIATE transaction, so it is atomic against concurrent append_message
    (mirrors try_acquire_compression_lock). Returns True iff rows were written."""
    def _do(conn):
        (n,) = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE session_id = ?", (session_id,)
        ).fetchone()
        if n:
            return False
        # ... INSERT each row (same column set as append_message) ...
        # ... UPDATE sessions SET message_count = ? WHERE id = ? ...
        return True
    return self._execute_write(_do)
```

**Gateway (`SessionStore`):** thin wrapper `seed_transcript_if_empty(session_id, rows)`
delegating to `self._db`, mirroring `rewrite_transcript`.

**Adapter (`_seed_session_from_band`):** replace the `load_transcript` re-check +
`rewrite_transcript` with a single `store.seed_transcript_if_empty(entry.session_id,
rows)`. Capability-guard: if the method is absent (older gateway), fall back to the
current best-effort re-check+rewrite (Phase C), then to the `channel_context` blob.
Return `True` when the durable path owned it (seeded or already non-empty), `False` on
absence/error.

`_can_seed_sessions` gains `seed_transcript_if_empty` to its required set when present;
otherwise the capability ladder degrades as above.

---

## 6. Decision → Hermes-source validation

| Decision | Validated against |
|---|---|
| The race is cross-thread, not loop-cooperative | `run.py:11078` executor turn; `run.py:9730` agent persists from the worker thread; `SessionDB._execute_write` `threading.Lock`/`BEGIN IMMEDIATE`/retry |
| Individual store ops are atomic; the check+act pair is not | `_execute_write` (per-op `BEGIN IMMEDIATE`); seed uses two separate calls |
| A single `BEGIN IMMEDIATE` txn is the right tool | `try_acquire_compression_lock` docstring ("SQLite serialises writes … atomic against other writers") |
| Compression lock is the wrong reuse | `append_message` path does not acquire it |
| Severity is bounded/self-healing | `_is_duplicate_replayed_user_message`; `delete_session_if_empty` / `prune_empty_ghost_sessions`; Band remains source of truth |
| `rewrite_transcript` is itself atomic (so today's clobber is bounded to one txn) | `SessionDB.replace_messages` docstring ("must commit as one transaction") |

---

## 7. Test plan

- **Concurrency test against the real `SessionDB`** (already proven instantiable on a
  temp DB): start the seed's check, then from a second thread `append_message` a turn
  row, then complete the seed. With Phase B's `seed_transcript_if_empty`: assert no
  clobber and no duplicate (the conditional insert no-ops because the row exists).
  Without it (today's path): the same harness demonstrates the clobber — keep it as a
  regression characterization, xfail-marked until B lands.
- **Capability ladder**: store with `seed_transcript_if_empty` → used; without it but
  with `rewrite_transcript` → Phase-C fallback; with neither → `channel_context` blob.
- Phase C: per-room lock serialises seed vs dispatch (no new dispatch observed mid-seed).

---

## 8. Retrospective — why we missed it

### Timeline

1. **Initial design** (`rehydration-design.md`): pitfalls P1–P10 covered double-rows,
   re-answers, version skew — all **data-contract** properties. No concurrency-model
   item.
2. **`/code-review` finding #4** *did* name a "seed vs concurrent live turn" race — but
   framed it as **asyncio interleaving** ("if the seed's appends interleave after the
   live turn began"). The fix (atomic `rewrite_transcript` + a re-check with no `await`
   between) closed the **loop-cooperative** window and was annotated "clobber-free."
3. That annotation **overclaimed**: it defended the wrong domain. The gateway writes
   transcripts from executor threads, which `BEGIN IMMEDIATE` serialises at the SQLite
   layer — a domain the loop-level guard never touches.

### Root cause (single statement)

**Every validation pass checked the gateway's *data contract* (method signatures, row
schema, key derivation, status semantics) but never its *execution / concurrency
model* — which thread writes the transcript, and in which serialization domain
atomicity must hold. So I reasoned about atomicity in asyncio-cooperative terms while
the gateway writes from executor threads, placing the entire cross-thread-race class
outside what the validation could see.**

Contributing factors:

- **"No `await` between ⇒ atomic"** is a correct heuristic in single-loop code and a
  silent trap the moment a second thread shares the resource. It was applied without
  asking "who else writes this, and on what thread?"
- **A "fixed" finding wasn't re-challenged.** Once review #4 was marked resolved, it
  left the lens; Alexander re-derived it from the *pattern* ("check-then-act smells
  like a race"), which is the heuristic the finding-by-finding review lacked.

### Process fixes (carry forward)

1. **Add an execution-model gate to the design checklist** for any change that does
   read-modify-write on shared gateway state: enumerate every writer and its thread,
   and state the serialization domain in which atomicity is claimed (asyncio loop vs
   SQLite transaction vs OS lock). A change is not "validated" until the data contract
   *and* the execution model are both checked.
2. **Race fixes must name the concurrent writer and the domain they defend**, and
   explicitly state what they do *not* defend (e.g. "guards single-loop interleave;
   does NOT guard cross-thread"). That phrasing would have surfaced this gap at fix
   time.
3. **Prefer the platform's own concurrency primitives** (here the `BEGIN IMMEDIATE` /
   advisory-lock pattern) over bespoke loop-level guards when touching shared state.

### The blind-spot signature (so we catch the *class*, not just this one)

Pattern-match future changes against this signature — any one is a trigger to run the
execution-model gate:

- a **check-then-act** on shared state (`load_* … then write_*`, "if not exists then
  create", count-then-insert);
- a write to **state the gateway also owns** (`_session_store`, the link, anything
  passed in via `set_*`);
- an `async` callback **invoked by the gateway** (could fire on a non-adapter loop —
  the INT-899 lesson);
- a claim of atomicity justified by **"no `await` between"** (only valid within one
  loop).

## 9. Audit — the same lens applied to the rest of the adapter

Re-scanning every adapter↔shared-state interaction with the execution-model lens
(not just the seed). This is the real test of the lesson.

| Site | Lens verdict | Detail (validated) |
|---|---|---|
| `_reset_room_session` → `store.reset_session` (`adapter.py:1063`) | **Real miss — fix** | Builds the key via `build_session_key(..., group_sessions_per_user=False)` directly — the *same key-derivation inconsistency* fixed in `_has_active_session` (#5) but left here. On a multiplexing / `gspu≠False` gateway it targets the *wrong* key, so close-on-leave silently fails to reset → stale history resumes on re-join (the exact thing rehydration exists to prevent). Benign on Band defaults; latent. Fix: derive via the store's `_generate_session_key` like `_has_active_session` does. (Surfaced by the "shared-key-derivation consistency across all call sites" lens.) |
| `store.reset_session` racing an in-flight turn for the same room | **Lower-risk — note** | Reset on the loop vs a turn-flush on the executor thread is the same cross-thread class as the seed, but `reset_session` is internally `_lock`-guarded and the broader "leave-mid-turn" invariant is gateway-owned (true for every platform). Out of scope; track if it surfaces. |
| `on_processing_start` / `on_processing_complete` `await self._link.mark_*` (`adapter.py:1297`,`1321`) | **Cleared — not a hazard** | These *are* gateway-invoked async callbacks (the INT-899 trigger shape), so the lens demanded a check. Verified benign: they're awaited on the gateway's main processing loop (`platforms/base.py:4188`,`4540`), which is `_link_loop` (set in `connect()` on that loop). `send()` needed `run_coroutine_threadsafe` only because the *startup-restore replay* runs on a different loop; the lifecycle hooks do not. No marshalling needed. |
| `_has_active_session` reading `_entries` / `load_transcript` (`adapter.py:1859`) | **Acceptable** | A read racing gateway writes can see a transient state, but it's only a best-effort *hint* (the seed's own guard is authoritative), so a stale read at worst defers/duplicates a flag — already covered by the seed's idempotency. |

Net: the lens found **one real latent bug** (`_reset_room_session` key derivation) and
correctly **cleared a false alarm** (the ack hooks) — demonstrating the gate catches the
class without crying wolf.
