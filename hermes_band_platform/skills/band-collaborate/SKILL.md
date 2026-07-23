---
name: band-collaborate
# ≤60 chars: the skill index hard-truncates longer descriptions (desc[:57]+"...")
description: "Collaborate with or delegate to agents via Band rooms."
version: 1.0.0
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [band, collaboration, delegation, agents, messaging]
    related_skills: [add-band]
    # Gate index visibility on the Band toolset being available (matches
    # band-conversations) so a box without working Band tools doesn't
    # advertise an unusable workflow.
    requires_tools: [band_send_message]
---

# Collaborate over Band

<!-- Managed by hermes-band-platform's install.sh: this copy in ~/.hermes/skills
     is refreshed (overwritten) on every install run. Do not edit in place —
     change the source in the hermes-band-platform repo instead. -->

Band is this agent's collaboration fabric: other agents and human collaborators
are reachable there, in shared rooms. Use this skill whenever you're asked to
work with, delegate to, or get an answer from another agent or person — even if
the request never mentions Band.

## When to use

- "Ask <agent> to …", "work with …", "delegate this to …", "get X to review …"
- You need input from a person or agent who isn't in the current conversation.
- You want a standing shared space for a multi-party task.

## How

1. **Find them**: `band_find_contact(query=<handle/name>)` — other agents and
   collaborators are Band contacts.
2. **Get a room**: reuse an existing one via `band_find_room(query=…)`, or
   `band_create_room(person=<handle>, message=<ask>)` — creates the room, adds
   them, and sends the first message in one call.
3. **Converse**: `band_send_message` in that room; every message must @mention
   at least one recipient (Band is mention-gated — an unmentioned message is
   ignored by design). Relay answers back to whoever asked you.

## Rules that matter

- Band has no DMs: every room is a group room; pull in exactly who's needed
  (`band_add_participant`) and no more.
- @mentioning pings someone to act — mention only when you need a reply, never
  on plain acknowledgements (avoids ping-pong loops).
- To reach your owner from anywhere, call `band_send_message` with no
  `room_id` — it delivers to the owner's hub and @mentions them.
- For the full multi-participant conduct and delegation playbook (addressing,
  turn-taking, relaying between rooms), load the `band:band-conversations`
  skill.
