# Federated LLM wiki query between friends

Design notes for the contacts and federation features: what they do, and why
they are built this way.

## Motivation

Hermes agents each maintain a local "LLM wiki" (Hermes core's bundled
`research-llm-wiki` skill — out of scope here, already works today). The goal
is to let a Hermes agent:

1. Connect to other Hermes agents ("friends") over Band — send a connection
   request, have the other side approve it.
2. Ask a question and get one answer synthesized from its own wiki plus every
   connected friend's wiki, without the user having to manually poll for
   replies.

`BandAdapter._consume()` wakes the Hermes agent loop
(`self.handle_message(...)`) the moment an `@mention` arrives, with no user
polling. That live-push property is what makes automatic (not manual-check)
federation possible, and is the basis for this design.

`band-sdk` already ships a complete native contacts subsystem
(`agent_api_contacts` REST endpoints, a `ContactTools` helper class, and
streaming `contact_request_received` / `contact_request_updated` /
`contact_added` / `contact_removed` events). Most of this feature is wiring
that up. The one piece of original logic is a deterministic state machine
that tracks a federated query across asynchronous replies and a timeout, then
triggers one synthesized answer.

## Part 1 — Connections (contacts)

`contacts.py` wraps `band.runtime.contact_tools.ContactTools` rather than
hand-rolling a second contacts system. The wrappers adapt its plain-dict
returns into the plugin's `tool_result` / `tool_error` envelope and follow
`tools.py`'s conventions (`_rest()` for the live link or an env-creds
fallback client, `_tool_exc()` for error shaping):

- `band_add_contact(handle, message=None)` — send a connection request.
- `band_list_contacts()` — list current approved contacts.
- `band_list_contact_requests()` — list pending received/sent requests.
- `band_respond_contact_request(action, request_id)` — approve / reject / cancel.

The two mutating tools go through `_authorize_band_action()`, same as the
mutating tools in `tools.py`.

### Adapter changes

1. `connect()` calls `subscribe_agent_contacts` (via
   `_subscribe_agent_contacts_safe`, best-effort so an older SDK without the
   channel cannot break messaging) alongside the existing
   `subscribe_agent_rooms`. The four contact event types land on the same
   internal queue `_consume()` already reads.
2. `_handle_event()` routes the four `contact_*` types to
   `_handle_contact_event`, which formats a human-readable line and delivers
   it with the same synthetic-injection idiom `_handle_participant_change`
   uses for "X joined this room" notices:

   ```
   [Contact Request] Alice (@alice/hermes) wants to connect.
   Message: "let's federate wiki searches"
   Request ID: abc-123
   ```

   Contact events carry no `room_id` (they are agent-level), so they always
   go to the Hub — the owner's one persistent control room.
3. If the Hub is not bootstrapped yet (`_hub_room_id is None`), contact
   events are logged and dropped rather than queued, mirroring the Hub's
   existing fail-closed posture for slash commands when the owner is
   unresolved.

The `band-contacts` skill teaches the Hub-resident LLM to report a
`[Contact Request]` to its owner and wait for an instruction before calling
`band_respond_contact_request`, unless the owner already stated a standing
auto-approve preference in that conversation.

## Part 2 — Federated wiki query

### Flow

```
Owner: "ask all my wikis about X"
  -> agent searches its own wiki locally (research-llm-wiki, unchanged)
  -> band_ask_wikis(query="X", local_findings=<own hits, or omitted>)
       -> resolves target friends: default = every contact with type "Agent";
          an explicit `friends` arg narrows it to named contacts
       -> creates a FRESH room (see "Why a fresh room" below), adds each
          friend, posts the query mentioning them all
       -> records a _PendingFederation and returns immediately with the
          room id, who was asked, and the timeout

Each friend's own live Hermes agent (a separate gateway process):
  -> wakes on the @mention (existing live-push behavior, unchanged)
  -> guided by the federated-wiki-search skill: searches its own wiki and
     replies in the room, explicitly passing `mention_ids` that include the
     asking agent. The default mention fallback ("all non-agent
     participants") would otherwise exclude the asking agent, since it is an
     Agent-type participant, and the reply would never be counted.

Requester's adapter, in the background:
  -> `_handle_message_created` recognizes the room as a pending federation
     (checked before normal mention-gating/dispatch) and routes to
     `_handle_federation_reply`: record the reply, and do NOT start a normal
     LLM turn in that room, so the requester's agent does not chatter back
     into the round-table after every reply
  -> once every expected friend has replied, OR FEDERATION_TIMEOUT_SECONDS
     (300) elapses, whichever is first: finalize by injecting one synthetic
     turn into the room `band_ask_wikis` was called from (the Hub, another
     Band room, or the Hub as fallback for a non-Band session), containing
     the local findings plus every reply — with "(no reply, timed out)" for
     stragglers — and asking the LLM for one consolidated answer.
```

### Why a fresh room per query

Replies are collected and synthesized automatically, so there is no
browsability benefit to reusing one persistent round-table room. Reusing a
room would also require correlating multiple concurrent queries inside it
(Band has no threads), which keying `_pending_federations` on `room_id`
avoids entirely: one room, one in-flight query.

### Where the code lives

`federation.py` owns only the "ask" side — resolving target contacts,
creating the room, adding participants, posting the mention-carrying query,
and registering the pending state. The state machine itself
(`_PendingFederation`, `register_pending_federation`,
`_handle_federation_reply`, `_federation_timeout`, `_finalize_federation`,
`format_federation_digest`) lives in `adapter.py`, because observing replies
requires the adapter's own event loop.

`_pending_federations: Dict[str, _PendingFederation]` sits alongside the
existing `_participants_cache` / `_last_human_sender` dicts — same idiom:
in-memory, resets on gateway restart (see Limitations).

The timeout is scheduled inside `register_pending_federation`, not in the
tool handler, so it outlives the tool call. Early completion cancels it and
awaits the cancellation, so the task is provably done before the caller
inspects state.

### Files

| File | Change |
|---|---|
| `hermes_band_platform/contacts.py` | New — the four contact tools. |
| `hermes_band_platform/federation.py` | New — `band_ask_wikis`. |
| `hermes_band_platform/adapter.py` | `subscribe_agent_contacts` in `connect()`; `contact_*` branches in `_handle_event()`; `_pending_federations` state, early intercept in `_handle_message_created`, and the timeout/finalize machinery; the five new tools and two new skills registered in `register()`. |
| `hermes_band_platform/skills/band-contacts/SKILL.md` | New. |
| `hermes_band_platform/skills/federated-wiki-search/SKILL.md` | New. |

No new environment variables — this feature needs no new credentials, and
the timeout is a fixed constant.

## Edge cases

- Non-`Agent` contacts (humans) are excluded from the default fan-out; naming
  one explicitly in `friends` surfaces a `warning` in the result rather than
  silently dropping it.
- Zero agent-type contacts → `band_ask_wikis` fails fast pointing at
  `band_add_contact`, and no room is created.
- A friend that never replies (offline, crashed) is covered by the timeout
  and reported as "(no reply, timed out)" in the digest. A query is never
  left open indefinitely.
- Any failure before `register_pending_federation` leaves no orphaned pending
  entry, which is why the tool's call order (resolve → create → add → post →
  register) is load-bearing.

## Limitations

- `_pending_federations` is in-memory only, so a gateway restart mid-query
  orphans that query and any late replies. Consistent with the adapter's
  other in-memory-only state (`_seen_inbound_ids`, `_last_human_sender`).

## Out of scope

- Any change to Hermes core's `research-llm-wiki` skill.
- Persisting `_pending_federations` across restarts.
- A configurable timeout — fixed at 5 minutes, favoring simplicity over a
  config knob nobody asked for yet.
- `band_remove_contact` — `ContactTools` supports it, but the stated use case
  does not need it; it can be added later following the same pattern.
