---
name: add-band
description: "Connect Hermes to Band end-to-end."
version: 1.0.0
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [band, messaging, onboarding, setup, integration]
    related_skills: [webhook-subscriptions]
---

# Add Band Skill

Connect a Hermes agent to Band from install through verification. This skill sets up the Hermes side and can optionally mint a Band external agent when the user provides a temporary user API key, but it does not keep user-level credentials after registration.

Band rooms are mention-gated and Band owns access control. The plugin creates a private Hermes Hub room on first gateway connect and stores only agent-scoped credentials in Hermes.

## When to Use

- The user asks to connect Hermes to Band or install the Band platform.
- A fresh Hermes gateway needs Band credentials, plugin enablement, gateway restart, and hub verification.
- The user wants the optional Band action toolset after chat already works.

Do not use this skill for ordinary Band chat troubleshooting after setup; inspect gateway logs and the adapter state directly instead.

## Prerequisites

- Hermes is installed and the gateway runs in the target Python environment.
- The `hermes-band-platform` plugin is installed by directory, pip, or Nix.
- The Band SDK is importable in the same Python environment as the gateway.
- The user can provide either:
  - Band agent credentials from `app.band.ai/agents/new`: `BAND_AGENT_ID` and `BAND_API_KEY`, or
  - a short-lived user API key in `BAND_USER_API_KEY` for automated Enterprise registration.

Never ask the user to paste a Band user API key into a command line. Ask them to set it as `BAND_USER_API_KEY` for the one registration step, then remove it.

## How to Run

Use the `terminal` tool for commands and the helper scripts shipped with this skill. Run scripts with the same Python interpreter that runs Hermes.

Skill helper paths are relative to this skill directory:

- `scripts/register_agent.py`
- `scripts/verify_install.py`
- `scripts/verify_gateway.py`

The setup scripts emit JSON so you can inspect success, missing checks, and next actions without exposing secrets.

## Quick Reference

Directory plugin install:

```bash
hermes plugins install band-ai/hermes-band-platform --enable
```

Pip install:

```bash
pip install hermes-band-platform
hermes plugins enable band
```

Verify install:

```bash
python scripts/verify_install.py
```

Optional Enterprise registration:

```bash
python scripts/register_agent.py
```

Verify gateway and hub:

```bash
python scripts/verify_gateway.py
```

## Procedure

1. Confirm the plugin is installed and enabled.
   - For directory installs, use `hermes plugins install band-ai/hermes-band-platform --enable`.
   - For pip installs, use `pip install hermes-band-platform`, then `hermes plugins enable band`.
   - For Nix installs, ensure the package and `band-sdk` are in the gateway Python environment and `plugins.enabled` contains `band`.

2. Verify the local install with `scripts/verify_install.py`.
   - If `sdk_importable` is false, install `band-sdk>=1.0.0,<2.0.0` into the gateway environment.
   - If `plugin_enabled` is false, enable `band`.
   - If credential checks are false, continue to credential collection.

3. Collect agent credentials.
   - Preferred manual path: have the user create an external agent at `app.band.ai/agents/new`, then run the Band setup wizard or save `BAND_AGENT_ID` and `BAND_API_KEY` with Hermes's env writer.
   - Optional Enterprise path: ask the user to set `BAND_USER_API_KEY`, run `scripts/register_agent.py`, confirm it saved `BAND_AGENT_ID` and `BAND_API_KEY`, then ask the user to remove `BAND_USER_API_KEY`.
   - Never persist `BAND_USER_API_KEY`.

4. Restart the gateway.
   - Use the user's normal Hermes gateway restart command.
   - On first connect the adapter resolves the owner, creates the Hermes Hub room, writes `BAND_HUB_ROOM`, and wires the hub as the home channel.

5. Verify the gateway with `scripts/verify_gateway.py`.
   - Success means the hub exists, gateway logs show Band connection signals, and no known hub failure signal appears in recent logs.
   - If the owner is unresolved, have the user set `BAND_OWNER_ID` and restart.
   - If no Band log signals appear, re-run install verification and confirm the gateway process is using the expected Python environment.

6. Ask the user to complete the Band UI loop.
   - They should open the Hermes Agent Hub room, @mention the agent, and confirm the agent replies.
   - Remind them that Band has no DMs; an unmentioned message is ignored by design.

7. Optionally enable Band action tools.
   - Add the `band` toolset to each platform that should act on Band.
   - Keep `hermes-band` for the messaging channel and add `band` only where action tools are needed.
   - Mutating tools remain owner-gated by the plugin.

## Pitfalls

- Installing the pip package but forgetting `hermes plugins enable band` leaves the entry point discovered but inactive.
- Installing a directory plugin without `band-sdk` in the gateway environment makes the platform unavailable at runtime.
- Saving `BAND_AGENT_ID` with generic config-setting commands can route it to config YAML instead of Hermes `.env`; use the setup wizard or Hermes env writer.
- Treating a successful WebSocket connect as full setup is incomplete; the hub must also be created and persisted as `BAND_HUB_ROOM`.
- Leaving `BAND_USER_API_KEY` in the environment after registration unnecessarily keeps a broad user credential live.
- Setting `BAND_HOME_ROOM` to another room means cron and notifications use that room instead of the hub.

## Verification

- `scripts/verify_install.py` reports package or directory plugin presence, SDK importability, plugin enablement, and required credential presence.
- `scripts/verify_gateway.py` reports `BAND_HUB_ROOM`, recent Band gateway success signals, and known failure signals.
- The user confirms the Hermes Agent Hub room exists in Band and an @mention test message round-trips.
