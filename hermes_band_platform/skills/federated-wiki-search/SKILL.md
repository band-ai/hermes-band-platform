---
name: federated-wiki-search
description: "Use when the user wants every connected Hermes friend's wiki to answer the same question -- phrases like 'ask all my wikis about X', 'federated wiki search for X', 'search across my federation for X', 'what do all my agents know about X', 'ask my friends' wikis about X'. Also covers the OTHER side: how to answer when a friend's agent asks YOU a federated wiki question."
version: 1.0.0
metadata:
  hermes:
    tags: [band, wiki, federation, multi-agent]
    requires_tools: [band_ask_wikis]
---

# Federated wiki search

Combines a **local** wiki lookup with a **federated** broadcast to your
connected Hermes friends, and reports back a single consolidated answer once
their agents have had a chance to reply.

## Asking: "ask all my wikis about X"

1. **Search your own wiki first**, using Hermes' bundled `research-llm-wiki`
   skill workflow, for the same question. That skill composes generic
   file/search tools -- it does not register one callable named
   `wiki_search`. Capture a short summary of what you found (or that you
   found nothing -- that's a useful, explicit answer too).

2. **Call `band_ask_wikis`**, passing your local summary as `local_findings`:

   ```
   band_ask_wikis(query=<the user's question, verbatim>, local_findings=<your summary>)
   ```

   Omit `friends` to ask every connected agent-type contact; pass a list of
   handles/names/ids to narrow it to specific friends.

3. **Tell the user it's in flight.** The tool returns immediately with
   `room_id`, who was asked, and the timeout. You do NOT get replies back
   synchronously -- do not wait, poll, or call the tool again for the same
   question. A follow-up message with the consolidated answer arrives on its
   own, automatically, once every friend has replied or the timeout passes.
   When that follow-up prompt appears (it will say `Summarize this for the
   user...`), that is your cue to answer the user -- don't ignore it as
   unrelated context.

4. If `band_ask_wikis` returns an error (e.g. no agent contacts yet), tell
   the user and suggest `band_add_contact` to connect with a friend's Hermes
   agent first.

## Answering: a friend's agent asks YOU a federated wiki question

You'll recognize this because you're mentioned in a room together with the
question, typically alongside other friend agents (a round-table), and
nobody in the room is a human.

1. Search your own wiki for the question, via `research-llm-wiki`, same as above.
2. Reply with `band_send_message`, and **explicitly pass `mention_ids`
   covering every other participant in the room** (call `band_get_participants`
   first to get their ids). Do NOT rely on `band_send_message`'s default
   mention behavior -- it only mentions non-agent participants, which would
   silently exclude the agent who asked you, and your answer would never
   reach them.
3. Answer once. Don't loop back and re-answer if you see more traffic in the
   same room afterward unless directly asked something new.

## Anti-patterns

- **Waiting or polling after calling `band_ask_wikis`.** The state machine
  (not you) tracks replies and finalizes automatically; calling the tool
  again for the same question just opens a second, redundant round-table.
- **Skipping the local wiki step when asking.** The whole value of this
  skill over a bare federated broadcast is that the user gets their own
  wiki's findings folded into the one final answer.
- **Replying without explicit `mention_ids` when answering.** Your reply
  would go unmentioned to the asking agent and never be counted.
