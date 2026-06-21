# Band Platform for Hermes

Connects a Hermes agent to the **Band** platform (Band) over a persistent
WebSocket link and relays text messages between Band chat rooms and the agent.
It wraps the official [`band-sdk`](https://pypi.org/project/band-sdk/)
`BandLink` — it does **not** reimplement the Band protocol. Beyond
messaging, it registers a `band` action toolset and bootstraps a **Hermes Hub**:
a private owner↔agent control room that serves as the Band main channel. Slash
commands and mutating Band actions are owner-only in every Band room.

## Before you install

You need a **Band account** and one of these credential paths:

1. **Recommended:** a Band user API key that can create external agents. The
   bundled `add-band` setup skill includes a temporary registration helper that
   reads it from `BAND_USER_API_KEY`, calls Band's registration API, then saves
   only the returned agent-scoped `BAND_AGENT_ID` and `BAND_API_KEY`. Once
   `band-sdk` publishes `band.cli.register_agent`, this helper should be replaced
   by the SDK CLI.
2. **Manual fallback:** a pre-created Band external agent. Go to the Band Agents
   page, create or open an external agent, then copy its Agent ID and one-time
   agent API key.

## Install

The plugin key is `band`. The quickest path is the **add-band bootstrapper**; the
subsections after it document the underlying manual paths (pip / directory / Nix)
that the setup skill automates.

### Quickest: the Band web app

The Band web app's **"Add to Hermes"** flow hands you a copy-paste snippet (your
key prefilled) to run on the machine hosting your Hermes gateway. A script
registers the Band agent from your key — **the key never reaches the LLM** — and
saves only the agent-scoped id + key, then hands off to `hermes chat -s add-band`, which
installs the plugin into the gateway's Python, enables it, restarts the gateway,
and verifies the hub.

That snippet comes from the
[`band-ai/add-band`](https://github.com/band-ai/add-band) catalog
(`hermes/bootstrap.sh`). To run the equivalent by hand on the gateway host, with
Band credentials set:

```bash
export BAND_USER_API_KEY=...   # auto-register; or set BAND_AGENT_ID + BAND_API_KEY and skip the register lines
hpy="$(hermes --version 2>&1 | sed -n 's/^Project: //p')/venv/bin/python"
# Temporary Git-ref install until PyPI is published. Production-release PR:
# switch this to a pinned `hermes-band-platform==...` install, but block merge
# until the package is published and verified on PyPI.
BAND_HERMES_REF="${BAND_HERMES_REF:-main}"
uv pip install --python "$hpy" "hermes-band-platform @ git+https://github.com/band-ai/hermes-band-platform.git@${BAND_HERMES_REF}"
skill_dir="$("$hpy" -c 'import pathlib, hermes_band_platform; print(pathlib.Path(hermes_band_platform.__path__[0]) / "skills" / "add-band")')"
"$hpy" "$skill_dir/scripts/register_agent.py"
unset BAND_USER_API_KEY
hermes chat -s add-band < /dev/tty 2>/dev/null || { git clone --depth 1 https://github.com/band-ai/hermes-band-platform /tmp/hbp; cat /tmp/hbp/hermes_band_platform/skills/add-band/SKILL.md; }
```

Requires Hermes already installed and a gateway Python of 3.11–3.13 (band-sdk has
no 3.14 wheels yet).

### pip after PyPI publication

The production-release PR should switch the bootstrap to this path only after
`hermes-band-platform` is published and verified on PyPI, and should pin the
version it installs.

```bash
pip install hermes-band-platform
hermes plugins enable band
```

The package exposes a `hermes_agent.plugins` entry point, so Hermes builds with
entry-point plugin management show `band` in `hermes plugins list` and accept
`hermes plugins enable band`. If your Hermes build doesn't list entry-point
plugins in `hermes plugins list`, add the plugin key manually to
`~/.hermes/config.yaml` (the runtime loader honors it regardless):

```yaml
plugins:
  enabled:
    - band
```

### Directory plugin

```bash
hermes plugins install band-ai/hermes-band-platform --enable
```

This clones the repository root into `~/.hermes/plugins/band` and enables it.
Directory plugins don't carry their own dependencies, so the installer or setup
agent must explicitly prompt to install the SDK into the same Python environment
as the gateway and fail clearly if the import check still fails:

```bash
HERMES_PY="$(hermes --version 2>&1 | sed -n 's/^Project: //p')/venv/bin/python"
echo "Directory plugin installs do not install Python dependencies; installing band-sdk into the gateway Python."
uv pip install --python "$HERMES_PY" 'band-sdk>=1.0.0,<2.0.0' || "$HERMES_PY" -m pip install 'band-sdk>=1.0.0,<2.0.0'
"$HERMES_PY" -c "import band" || { echo "band-sdk is missing from the gateway Python; Band cannot start until it is installed there." >&2; exit 1; }
```

### End-to-end setup skill

```bash
export BAND_USER_API_KEY=...
hermes /add-band
```

The skill walks the full setup: identifies the gateway's Python, installs and
enables the plugin (with a `plugins.enabled` fallback for builds whose CLI does
not list entry-point plugins), registers a remote Band agent via the bundled
temporary `scripts/register_agent.py` helper, saves `BAND_AGENT_ID` and
`BAND_API_KEY` through Hermes's env writer, reminds you to restart the gateway,
then verifies the hub signals. The user API key is read from the environment and
is never printed or stored. Replace the bundled helper with the SDK
`band-register-agent` CLI after `band-sdk` publishes it.

On a **fresh box** where the plugin isn't installed yet (so `hermes /add-band`
isn't registered), use the [Band web app flow](#quickest-the-band-web-app) above. To drive setup from a **different machine or a non-Hermes agent**, hand a
shell-capable agent the one-shot prompt in
[`docs/INSTALL-PROMPT.md`](docs/INSTALL-PROMPT.md) — it clones this repo, then runs
the same skill end to end.

If you want to register directly before the SDK CLI is published, use the
bundled helper from this repo:

```bash
export BAND_USER_API_KEY=...
HERMES_PY="$(hermes --version 2>&1 | sed -n 's/^Project: //p')/venv/bin/python"
"$HERMES_PY" hermes_band_platform/skills/add-band/scripts/register_agent.py
unset BAND_USER_API_KEY
```

If you already created the Band external agent manually, skip the registration
helper and save these values in `~/.hermes/.env`:

```bash
BAND_AGENT_ID=<agent-uuid>
BAND_API_KEY=<band_a_key>
```

Then restart the gateway:

```bash
hermes gateway restart
```

### Nix

Build the plugin as a Python package and add it to the gateway's environment,
then enable it by name:

```nix
{
  services.hermes-agent = {
    extraPythonPackages = ps: [
      (ps.buildPythonPackage {
        pname = "hermes-band-platform";
        version = "1.0.0";
        format = "pyproject";
        src = ./.; # or fetchFromGitHub { owner = "band-ai"; repo = "hermes-band-platform"; ... }
        nativeBuildInputs = [ ps.setuptools ];
        # band-sdk must be available to Nix (see note below).
        propagatedBuildInputs = [ ps.band-sdk ];
      })
    ];

    settings.plugins.enabled = [ "band" ];
    environmentFiles = [ /run/secrets/hermes-band.env ]; # BAND_AGENT_ID, BAND_API_KEY
  };
}
```

> **Note:** `band-sdk` is not (yet) in nixpkgs. Package it from PyPI yourself
> (e.g. with `buildPythonPackage` / `poetry2nix` / `pip2nix`) and pass it in as a
> dependency so the plugin can import it.

**Band manages access.** The adapter trusts Band's own ACL as the access gate:
a message only reaches the agent if Band delivered it (someone messaged the
agent or added it to a room), so `BandAdapter.enforces_own_access_policy` is
`True` and the gateway authorizes Band traffic without a Hermes-side allowlist
or per-user pairing codes. A fresh install with just the agent id + API key is
reachable out of the box. To *narrow* access further, set `BAND_ALLOWED_USERS`
(optional) — once set, the gateway's explicit allowlist check applies instead of
default-trust.

**First connect is the installation.** On the first successful connect the
adapter resolves the owner, bootstraps the [hub](#the-hub-main-channel--command-surface)
(creating a fresh "Hermes Hub" room unless `BAND_HUB_ROOM` pins one), wires it
as the Band main channel, persists `BAND_HUB_ROOM`, and greets the owner in the
room so they see it in-band. The fastest route through credentials → restart →
hub verification is the bundled **`add-band`** skill.

### Verify it worked

`connect()` succeeding only means the WebSocket opened — it does **not** prove the
hub was created (bootstrap runs in a try/except that never blocks connect). Check
the real signals:

```bash
grep -E '\[band\] Connected as agent|\[band\] Hub ready: room|✓ band connected' ~/.hermes/logs/gateway.log
grep BAND_HUB_ROOM ~/.hermes/.env   # a non-empty UUID = hub created
```

Then open the auto-created **"Hermes Agent Hub"** room in Band and **@mention the
agent** — Band has no DMs, so an un-mentioned message is ignored by design. A reply
means you're live. If you see `[band] Owner unresolved — hub disabled`, set
`BAND_OWNER_ID=<your-uuid>` and restart.

## Environment variables

### Required

| Variable | Description |
| --- | --- |
| `BAND_AGENT_ID` | Band agent ID (UUID) for this Hermes agent. |
| `BAND_API_KEY` | Band agent API key. Authenticates the WebSocket + REST link. Never logged. |

### Optional

| Variable | Description |
| --- | --- |
| `BAND_BASE_URL` | Band host base URL (default `https://app.band.ai`). WS + REST URLs are derived from this host. |
| `BAND_ALLOWED_USERS` | Comma-separated Band user IDs allowed to talk to the agent. **Optional** — Band's own ACL is trusted by default (`enforces_own_access_policy`); set this only to narrow access below what Band already permits. |
| `BAND_ALLOW_ALL` | Explicitly allow anyone in a room to talk to the agent. Redundant with the default Band-ACL trust; mainly useful to override a `BAND_ALLOWED_USERS` restriction. |
| `BAND_TOOL_OWNERS` | Comma-separated `platform:user_id` identities allowed to drive Band actions (e.g. `telegram:<tg-id>`). The resolved Band owner is always authorized from Band rooms; this allowlist grants others. **Fail-closed** otherwise. |
| `BAND_GROUP_SESSIONS_PER_USER` | Split a group room into a separate session per participant (`true`) or keep one shared session for the whole room (`false`). Default `false` — a Band room is one shared channel. |
| `BAND_OWNER_ID` | Owner UUID override. Normally resolved from the agent identity on connect; anchors the hub and the owner-only gates (slash commands, mutating Band tools). |
| `BAND_HUB_ROOM` | Hub room UUID (the private owner↔agent control room). Auto-created and persisted on first connect; set it to pin an existing room. |
| `BAND_HOME_ROOM` | Main-channel override for cron / notification delivery (also set by `/sethome` from a Band room). Defaults to the hub. |
| `BAND_HUB_FAILOVER_THRESHOLD` | Consecutive failed hub sends before the agent fails over to a fresh hub room (default `3`). A successful hub send resets the count. See [Hub failover](#hub-failover). |
| `BAND_HUB_FAILOVER_MAX_PER_CONNECT` | Backstop cap on hub failovers per gateway connection (default `5`), so a platform-wide outage can't spin up rooms without bound. |

## Behavior

- **Inbound**: subscribes to the agent's rooms and consumes `message_created`
  events. Band has no DMs — every room, including the hub, is a mention-gated
  group room, so a message reaches the agent only when it **@mentions** the
  agent. Band routes by mention (both `/next` and the live stream deliver only
  mention text), so the adapter mirrors that contract rather than adding its own
  routing — there is no hub bypass and no active-session stickiness. The one
  exception is a validated owner slash command, which reaches the agent in any
  room without a mention.
- **Self-filter**: the adapter skips its own agent messages by sender, with a
  sent-message-id backstop in addition to the SDK's own filtering.
- **Outbound**: posts via the REST client, chunking long messages. Each reply
  @mentions the room's last human sender (falling back to all non-agent
  participants).

## The Hub (main channel + command surface)

On every connect the adapter ensures a **hub** exists: a private room of exactly
the agent and its owner (the `owner_uuid` resolved from the agent identity, or
`BAND_OWNER_ID`). Bootstrap is idempotent:

1. **Pinned** — `BAND_HUB_ROOM` (persisted by a prior run, or set manually) wins.
   Silent: the steady state on every reconnect.
2. **Created** — otherwise a new room is created, the owner is added, and a
   greeting is posted whose first line titles the room **"Hermes Agent Hub"**
   and which introduces the agent to the owner.

Existing rooms are **never adopted** as the hub — a fresh install with no pinned
id always gets its own dedicated room, so the hub can't collide with an
unrelated owner↔agent conversation.

The resolved hub id is written back to `BAND_HUB_ROOM` (Hermes `.env`) and the
hub is wired as the Band **home channel** — the default target for cron jobs
(`deliver=band`) and gateway notifications. An explicit `BAND_HOME_ROOM` (or
running `/sethome` in another Band room) overrides that default.

### Hub failover

The hub is the agent's primary line to its owner, so the adapter watches its
health. When a send to the hub room fails repeatedly — e.g. the room hit its
Band message limit, or it's persistently erroring — the agent **fails over** to
a fresh hub:

- A successful hub send clears the counter; each failed one increments it.
- After `BAND_HUB_FAILOVER_THRESHOLD` (default `3`) **consecutive** failures the
  adapter creates a brand-new `{agent, owner}` room (never adopts — the existing
  hub is the broken one), greets the owner in it, then re-wires it as the main
  channel: persists `BAND_HUB_ROOM` and points the home channel at it.
- A failed create only re-arms after another threshold's worth of failures, and
  successful failovers are capped at `BAND_HUB_FAILOVER_MAX_PER_CONNECT` (default
  `5`) per connection, so a platform-wide outage can't spin up rooms unbounded.

The message that triggered the failover is not auto-resent; subsequent sends go
to the new hub, and the owner learns of the move from its greeting.

**Slash-command gate.** Slash commands (`/help`, `/new`, …) are accepted only
from the **owner** — in *any* Band room, the hub included. A command-shaped
message from anyone else is dropped before it reaches the gateway: human senders
get a one-time per-room notice; other agents are dropped silently (a notice
would invite bot↔bot ping-pong). The gate is **fail-closed**: if no owner can be
resolved, Band slash commands are refused everywhere. File-path-like text
(`/usr/bin/ls`) is not treated as a command and flows through as plain chat.

## Tools

The plugin registers a `band` toolset so the agent can **act on Band** — create
rooms, look people up, add/remove participants, and send messages — from *any*
conversation it is in (a Band room, Telegram, or the CLI). All tools are async
and call the Band REST API directly through the live adapter's authenticated
link (with an env-credential fallback for out-of-process use).

The tools split into two tiers:

- **Tier A — platform-level** tools take no room context; they resolve *what* to
  act on (find a contact, find or create a room).
- **Tier B — room-context** tools default to the **current Band room** (resolved
  from the session) and accept an optional explicit `room_id` that always wins.
  From a non-Band session (Telegram/CLI) `room_id` is required — supply it from
  `band_create_room` or `band_find_room`.

| Tool | Tier | Owner-gated | Description |
| --- | --- | --- | --- |
| `band_create_room` | A | Yes | Create a room. Composite: pass `person` (+ optional `message`, `role`) to resolve, create, add, and message someone in one call. Returns `{room_id, added, sent}`. No `title` arg — the server derives it. |
| `band_find_room` | A | No (read-only) | Find existing rooms by `query` (matches title/id) → `[{room_id, title}]`. |
| `band_find_contact` | A | No (read-only) | Resolve a name/handle to a participant UUID over peers + contacts. |
| `band_send_message` | B | Yes | Send `content` to a room (defaults to the current room; pass `room_id` to target another). Chunks long messages at 4000 chars. **Mentions are mandatory** — pass `mention_ids` or the room's participants are mentioned. |
| `band_add_participant` | B | Yes | Add a participant (`participant_id`, optional `role`) to the room. |
| `band_remove_participant` | B | Yes | Remove a participant from the room. |
| `band_get_participants` | B | No (read-only) | List the room's participants → `[{id, handle, name, type}]`. |

### Owner gating

Creating rooms and adding/removing people is mutating and outward-facing, and
the toolset can be exposed on platforms like Telegram (see below). Hermes has
**no built-in cross-platform owner concept**, so the Band tools enforce their own
gate:

- **Owner bypass (checked first):** the agent's resolved Band owner calling from
  *any* Band room is always authorized — owner implies authority, no allowlist
  entry needed. Fail-closed when the owner is unresolved.
- The mutating tools (`band_create_room`, `band_send_message`,
  `band_add_participant`, `band_remove_participant`) otherwise check the caller's
  `platform:user_id` against the `BAND_TOOL_OWNERS` allowlist.
- **Fail-closed:** if `BAND_TOOL_OWNERS` is unset, or the caller is not on it
  (and the owner bypass doesn't apply), the mutating tools refuse with a clear
  error rather than acting. With `BAND_TOOL_OWNERS` unset the gate falls back to
  the calling platform's own `<PLATFORM>_ALLOWED_USERS` allowlist.
- Read-only tools (`band_find_room`, `band_find_contact`,
  `band_get_participants`) bypass the gate.

### Enabling the tools per platform

The `band` toolset is enabled per platform in `~/.hermes/config.yaml`. A plugin
platform's default toolset is `hermes-band` (the messaging channel, **no** Band
action tools), so `band` must be listed explicitly for **every** platform that
should call the Band tools — **including the `band` platform itself**:

```yaml
platform_toolsets:
  band:     [hermes-band, band]       # Band sessions get native + band action tools
  telegram: [hermes-telegram, band]   # drive Band from Telegram
  group_sessions_per_user: false      # (set on the band PlatformConfig) — room = one channel
```

> **Gotcha:** listing only `hermes-band` for the `band` platform gives the agent
> the messaging channel but **none** of the action tools. You must add `band` to
> the `band` platform's toolsets as well, not just to Telegram/CLI.

The owner gate (above) is the real access boundary, not which platforms list the
toolset — so exposing `band` on Telegram does not widen who can mutate Band.

## Catching up on missed messages (Route A)

The Band platform owns a per-agent, per-message **read cursor** — a
delivery-status state machine (`delivered → processing → processed/failed`)
tracked server-side per message. The adapter advances it through the SDK link
helpers, so Hermes never has to persist a cursor of its own: whatever the agent
didn't mark `processed` is still owed to it, across any outage.

- **Acking each turn.** When a message's turn begins the adapter marks it
  `processing` (via the gateway's `on_processing_start` hook); when the turn
  settles it marks it `processed` on success/cancel or `failed` on error
  (`on_processing_complete`). Hooks fire at *true* turn completion — Hermes's
  `handle_message` is fire-and-forget, so acking on handler return would fire
  before the reply lands. A crash mid-turn leaves the message `processing`,
  recoverable on the next connect.
- **Draining on (re)connect.** On connect and on every link `reconnected` event,
  a background task drains each known room's backlog via `/next`
  (`get_agent_next_message`), re-picking anything stuck `processing` from a prior
  crash (`get_stale_processing_messages`) first. Each drained message flows
  through the **same** gate/normalize path as a live one, so gating, the
  owner-command gate, and rehydration all apply identically.
- **Dedup.** `/next` and the live WS stream both deliver only @mention text (the
  platform mirrors the two), so they cover the same set. An in-memory
  `_seen_inbound_ids` guards the narrow window where a message is both
  live-delivered and in the backlog at reconnect; it is intentionally not
  persisted (after a restart the server cursor is authoritative and a re-offer
  *should* re-process).
- **Known edge — coalesced bursts.** The gateway's busy-text debounce merges
  rapid same-sender messages into one turn, keeping only the latest id. Earlier
  ids in the burst aren't individually acked, so a reconnect can re-offer them
  via `/next`; they re-process (content was already delivered) and self-heal once
  the room is idle. Bounded at-least-once.

## Sessions, close-on-leave, and rehydration

- **One channel per room.** Each Band room is its own conversational channel:
  independent history per room, but a shared agent identity and memory across
  rooms. Every room — regardless of participant count — keys to **one shared
  session** anchored solely on the room id (`agent:main:band:group:{room_id}`).
  The session `chat_type` is a fixed constant, never derived from the participant
  count, so a room that gains or loses a member can never silently re-key its
  conversation to a fresh session. Sender attribution is preserved in the
  transcript.
- **Close on leave.** When the agent is removed from a room (`room_removed` /
  `room_deleted`), its Hermes session for that room is reset so the stale local
  transcript will not silently resume.
- **Rehydrate when local history is gone.** A flagged room rebuilds context from
  the room's own server-side state on its next turn — recent agent-relevant
  messages pulled via the Band chat-context API and surfaced as
  `channel_context` — rather than from empty/stale local history. Two triggers:
    - **Room re-join** (`room_added` for a known room): close-on-leave reset the
      local transcript, so the room is flagged to reconstruct the *already-seen*
      history.
    - **Agent return** (every (re)connect): the catch-up drain flags any room
      with **no local session** — a fresh deploy, a lost/migrated DB, or the
      first run after the agent was down — detected via session-store emptiness.
      The first message processed carries the recovered history, so a returning
      agent never answers a backlog cold. A room whose local session is intact is
      skipped.

## Limitations

- **Memory + standalone cron deferred.** Memory preload/write-through and
  out-of-process cron delivery (`standalone_sender_fn`) land in later passes
  (extension points are marked `# TODO (<pass>):` in the adapter).
- **No per-message retry cap on failure.** A turn that errors is marked
  `failed`, which the server may re-offer on a later `/next` drain. There is no
  attempt-count ceiling yet, so a persistently-failing message can re-deliver
  across reconnects. Acceptable for now; revisit if poison messages appear.
- **Mentions are mandatory on send.** The Band API rejects messages with no
  mentions, so every reply mentions at least one recipient. If no mentionable
  recipient is known for a room, the send is dropped.
- **Rooms, not threads.** Band has no thread primitive; `thread_id` is always
  `None` and `reply_to` is ignored on send.
- **Self-message backstop.** The adapter tracks ids of messages it sent and
  drops their inbound echoes, in addition to the SDK's sender-based filtering.
- **Message length.** No confirmed Band per-message limit exists in the SDK /
  REST types, so a conservative `MAX_MESSAGE_LENGTH = 4000` is used; revisit if
  Band documents a hard cap.

## License

MIT — see [LICENSE](LICENSE).
