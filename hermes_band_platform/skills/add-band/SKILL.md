---
name: add-band
description: "Use when a user wants to connect their Hermes agent to Band (Thenvoi). Bootstraps the full integration end-to-end: installs the thenvoi SDK, gets or mints Band credentials (programmatic agent registration with fallback to the manual Band UI), persists them, restarts the gateway, and verifies the auto-created Hermes Hub room — leaving the user able to chat with their Hermes agent through any Band chat."
version: 1.0.0
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [band, thenvoi, messaging, onboarding, setup, integration]
    related_skills: [webhook-subscriptions]
---

# Add Band (Thenvoi) to Hermes

## Overview

Connect this Hermes agent to **Band** (the Thenvoi messaging platform, `app.thenvoi.com`)
end-to-end. By the end: `BAND_AGENT_ID` + `BAND_API_KEY` are in `~/.hermes/.env`, the gateway
is connected to Band, a private **"Hermes Agent Hub"** room exists on Band as the agent's main
channel, and the user can chat with Hermes from any Band chat.

Almost everything is automatic once credentials exist. The Band adapter resolves the owner and
**creates the hub room itself** on first connect — you do not call an API to make it. Access is
governed by Band's own ACL (the adapter sets `enforces_own_access_policy=True`), so there is **no
Hermes-side allowlist and no pairing codes**: anyone Band lets reach the agent can chat immediately.

**The one thing no code can do is mint the credentials.** That needs a browser action at
`app.thenvoi.com`. Tell the user this up front, then drive everything else for them.

## When to Use

- A user asks to "connect Hermes to Band", "set up Band", "add the Band platform", or similar.
- You're onboarding a fresh Hermes install onto Band for the first time.

**Don't use for:** debugging an already-connected Band agent (read `~/.hermes/logs/gateway.log`
directly), enabling Band *tools* on an agent that already chats on Band (jump to the optional
toolset step), or any non-Band platform.

## Conventions used below

- `PY` = the Python interpreter that runs the gateway. In a repo checkout it's `.venv/bin/python`;
  in a `pip install` it's whatever `hermes` runs on. Run commands with that interpreter so imports
  resolve against the gateway's environment.
- `BASE` = `${BAND_BASE_URL:-https://app.thenvoi.com}` (only differs for self-hosted Band).

## Step 1 — Ensure the plugin is installed and enabled

Band needs the `thenvoi-sdk` package importable in the gateway's environment. If it's missing the
platform is **silently skipped** (gateway logs "running with 1 platform(s)" and no `[band]` lines).

```bash
PY -c 'import thenvoi; print("thenvoi", thenvoi.__version__)'
```

If that errors, install it:

```bash
pip install 'thenvoi-sdk>=0.2.9,<0.3'
```

> **uv-managed venv (dev checkouts):** if there is no `pip` in the venv, `pip install` fails. Use:
> `uv pip install --python .venv/bin/python 'thenvoi-sdk>=0.2.9,<0.3'`. Note `thenvoi-sdk` may not be in
> `uv.lock`, so a later `uv sync --locked` can remove it — re-run this import check if Band suddenly
> stops loading.

**Enablement.** A plugin dropped into `~/.hermes/plugins/band/` is opt-in and does **not** auto-load
(only plugins bundled under `plugins/platforms/` do). Enable it once:

```bash
hermes plugins list | grep -i band      # discovered? enabled?
hermes plugins enable band              # run if listed-but-not-enabled (skip for bundled builds)
```

Re-run the import check until it prints a version, and confirm the plugin is enabled, before continuing.

## Step 2 — Get and store Band credentials

You need `BAND_AGENT_ID` (a UUID) and `BAND_API_KEY` (`band_a_…`, shown once). **Credential
collection and storage are delegated to the Band setup wizard** — it prompts for both, masks the key,
writes them to `~/.hermes/.env` with the correct routing, and shows the right agent-creation path.
Don't hand-write `.env` or re-implement the prompts here.

### Default — run the wizard

Have the user run, in their own terminal:

```bash
hermes gateway setup        # then choose "Band" 🎵 from the platform list
```

The wizard walks them through creating the agent at `/agents/new` and pasting the **Agent ID** +
**API key** (there is no `hermes gateway setup band` subcommand — it's an interactive picker). When
they confirm they're done, verify the values landed:

```bash
PY -c "from hermes_cli.config import get_env_value as g; \
print('BAND_AGENT_ID', bool(g('BAND_AGENT_ID')), '| BAND_API_KEY', bool(g('BAND_API_KEY')))"
```

Both `True` → continue. If not, the wizard was cancelled or `.env` writes are blocked (managed mode);
re-run it, or have the user set the two vars by hand.

### Optional accelerator — register the agent for them (Enterprise)

To skip browser agent-creation, offer this *before* the wizard: ask for a **user** API key
(`band_u_…`, created at **Settings → REST API Keys**) and register the agent in one call:

```bash
curl -sS -X POST "BASE/api/v1/me/agents/register" \
  -H "X-API-Key: <band_u_key>" -H "Content-Type: application/json" \
  -d '{"agent":{"name":"Hermes","description":"Hermes AI gateway agent"}}'
```

On HTTP 201, read `.data.agent.id` and `.data.credentials.api_key`, then persist them **directly** —
the wizard can't mint, so the skill stores them here. Use `save_env_value`, **not** `hermes config
set` (`BAND_AGENT_ID` ends in `_ID`, which routes to `config.yaml`, not `.env`):

```bash
PY -c "from hermes_cli.config import save_env_value as s; \
s('BAND_AGENT_ID','<uuid>'); s('BAND_API_KEY','<band_a_key>')"
```

Discard the `band_u_` key — it's only for this call (name ≥ 3 chars, description ≥ 10). On **403**
(`plan_required`, non-Enterprise) or no user key → just use the wizard above.

## Step 3 — Restart the gateway

```bash
hermes gateway restart
```

On this connect the adapter resolves the owner from the agent identity, runs `_ensure_hub()`,
**creates the "Hermes Agent Hub" room on Band**, posts an @owner greeting, persists `BAND_HUB_ROOM`
to `~/.hermes/.env`, and wires the hub as the main channel — all automatic.

## Step 4 — Verify (machine-checkable)

`connect()` returning True only confirms the WebSocket — it does **not** prove the hub was created.
Check the real signals:

```bash
# 1. Hub room was created + persisted (non-empty UUID):
PY -c "from hermes_cli.config import get_env_value; print('BAND_HUB_ROOM=', get_env_value('BAND_HUB_ROOM'))"

# 2. Gateway log shows a healthy Band connect + hub:
grep -E '\[band\] Connected as agent|\[band\] Hub ready: room|✓ band connected' ~/.hermes/logs/gateway.log | tail

# 3. No failure lines:
grep -E '\[band\] Owner unresolved|\[band\] Hub bootstrap failed|requirements not met' ~/.hermes/logs/gateway.log | tail
```

Interpretation:
- `BAND_HUB_ROOM` set + `[band] Hub ready: room …` → **hub created, success.**
- `[band] Owner unresolved — hub disabled` → owner couldn't be resolved. Find the owner UUID in the
  Band profile, `save_env_value('BAND_OWNER_ID', '<uuid>')`, and restart.
- No `[band]` lines at all → SDK missing (back to Step 1) or credentials not loaded (re-check Step 2).

Optionally confirm the new agent key works:

```bash
curl -sS "BASE/api/v1/agent/me" -H "X-API-Key: <band_a_key>"   # returns the agent identity
```

## Step 5 — Confirm chat works (human-in-the-loop)

You can't see the Band UI, so ask the user to close the loop:

> Check your Band app — a **"Hermes Agent Hub"** room should have appeared with a greeting from the
> agent. **@mention the agent** and send a test message — Band has no DMs, so an
> un-mentioned message is ignored by design — and confirm it replies.

A round-tripped reply is the proof the integration is live.

## Optional — Enable the Band action toolset

Chatting works without this. If the user wants the agent to **act** on Band (create rooms, add
participants, message other rooms), add `band` to `platform_toolsets` in `~/.hermes/config.yaml`:

```yaml
platform_toolsets:
  band:     [hermes-band, band]      # act on Band from Band rooms
  telegram: [hermes-telegram, band]  # optional: drive Band from Telegram
```

Mutating tools are owner-gated; `BAND_TOOL_OWNERS` (`platform:user_id`, e.g. `telegram:123,band:<uuid>`)
grants others. Restart the gateway after editing. Then re-verify chat still works.

## Common Pitfalls

1. **Expecting the wizard alone to finish setup.** `hermes gateway setup` (pick "Band") collects and
   persists the two credentials — but it does **not** install the SDK, restart the gateway, register
   an agent, or verify the hub. The skill delegates credentials to the wizard and owns everything else.
2. **`hermes config set BAND_AGENT_ID`.** Routes to `config.yaml`, not `.env` — the adapter won't see
   it. Always use `save_env_value` for the ID.
3. **"Settings → Agents" for agent creation.** Wrong path — that page only has API keys. Agent creation
   is `/agents/new` (the Agents page).
4. **`pip install` in a uv-managed venv.** No `pip` binary there; use the `uv pip install` form.
5. **Trusting `connect()=True`.** The hub is created in a `try/except` that never blocks connect — verify
   via `BAND_HUB_ROOM` + the `Hub ready` log line.
6. **`BAND_HOME_ROOM` already set to another room.** The hub is still created but cron/notifications go to
   that other room instead. Warn the user and offer to clear it if they expected the hub to be the main channel.
7. **Persisting the `band_u_` user key.** Don't — it's a user-level credential with a wide blast radius and
   is only needed for the one-time register call.

## Verification Checklist

- [ ] `PY -c 'import thenvoi'` succeeds (SDK installed in the gateway's env)
- [ ] `BAND_AGENT_ID` + `BAND_API_KEY` present in `~/.hermes/.env` (`band_a_…` key, UUID id)
- [ ] `hermes gateway restart` ran cleanly
- [ ] `BAND_HUB_ROOM` is a non-empty UUID in `~/.hermes/.env`
- [ ] `gateway.log` shows `[band] Connected as agent` + `[band] Hub ready: room` + `✓ band connected`
- [ ] No `[band] Owner unresolved` / `[band] Hub bootstrap failed`
- [ ] User confirms the "Hermes Agent Hub" room appeared and an **@mention** test message round-trips
