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

- Hermes is installed and its gateway runs in a Python **3.11–3.13** environment (the Band SDK has no 3.14 wheels yet).
- You can run shell commands as the user who owns the Hermes install.
- The user can provide either:
  - Band agent credentials from `app.band.ai/agents/new`: `BAND_AGENT_ID` and `BAND_API_KEY`, or
  - a short-lived user API key in `BAND_USER_API_KEY` for automated Enterprise registration.

This skill installs the plugin and the Band SDK as part of the procedure — they need not be present beforehand.

Never ask the user to paste a Band user API key into a command line, and never read it yourself. The key is consumed by `register_agent.py` (a script that reads it from the environment) — ideally by the bootstrapper *before* this skill runs, so it never enters the agent's environment. Only the resulting agent-scoped `BAND_AGENT_ID` + `BAND_API_KEY` are stored; the user key is never printed or persisted. Have the user remove `BAND_USER_API_KEY` after the one registration step.

## How to Run

Use the `terminal` tool for commands and the helper scripts shipped with this skill. Run scripts with the same Python interpreter that runs Hermes.

Skill helper paths are relative to this skill directory:

- `scripts/register_agent.py`
- `scripts/verify_install.py`
- `scripts/verify_gateway.py`

The setup scripts emit JSON so you can inspect success, missing checks, and next actions without exposing secrets.

## Quick Reference

Identify the gateway interpreter first — every install and script call uses it:

```bash
HERMES_PY="$(hermes --version 2>&1 | sed -n 's/^Project: //p')/venv/bin/python"
```

Pip install (auto-installs `band-sdk`), then enable with a config fallback for builds whose CLI does not list entry-point plugins:

```bash
uv pip install --python "$HERMES_PY" hermes-band-platform || "$HERMES_PY" -m pip install hermes-band-platform
hermes plugins enable band 2>/dev/null && hermes plugins list | grep -qw band \
  || "$HERMES_PY" -c "from hermes_cli import plugins_cmd as C; s=C._get_enabled_set(); s.add('band'); C._save_enabled_set(s); print('enabled band via config')"
```

Directory plugin install instead (CLI-native, no entry-point caveat; install the SDK separately):

```bash
hermes plugins install band-ai/hermes-band-platform --enable
uv pip install --python "$HERMES_PY" 'band-sdk>=1.0.0,<2.0.0'
```

Verify install / register / verify gateway (always with the gateway interpreter):

```bash
"$HERMES_PY" scripts/verify_install.py
"$HERMES_PY" scripts/register_agent.py    # optional Enterprise registration
"$HERMES_PY" scripts/verify_gateway.py
```

## Procedure

1. Identify the Python interpreter that runs the gateway, and confirm its version.
   - The plugin must be installed into the *same* interpreter that runs `hermes`. Installing into any other environment leaves it undiscoverable even though `import hermes_band_platform` may succeed from the repo directory.
   - Derive and sanity-check it:
     ```bash
     command -v hermes || { echo "Hermes is not on PATH"; exit 1; }
     hermes --version   # note the reported Python — it must be 3.11–3.13
     HERMES_PY="$(hermes --version 2>&1 | sed -n 's/^Project: //p')/venv/bin/python"
     "$HERMES_PY" -c "import hermes_cli" || { echo "Locate the python that runs the gateway and set HERMES_PY"; exit 1; }
     ```
   - If the gateway Python is 3.14 or newer, stop and tell the user — `band-sdk` has no wheels for it yet.

2. Install the plugin into that interpreter.
   - pip / PyPI (auto-installs `band-sdk`):
     ```bash
     uv pip install --python "$HERMES_PY" hermes-band-platform || "$HERMES_PY" -m pip install hermes-band-platform
     ```
   - Directory install instead (CLI-native; the SDK is not bundled, so add it):
     ```bash
     hermes plugins install band-ai/hermes-band-platform --enable
     uv pip install --python "$HERMES_PY" 'band-sdk>=1.0.0,<2.0.0'
     ```
   - For Nix installs, ensure the package and `band-sdk` are in the gateway Python environment and `plugins.enabled` contains `band`.

3. Enable the plugin (skip if you used `hermes plugins install … --enable`).
   - Try the CLI; if the build does not list entry-point plugins, write the `plugins.enabled` config directly. The runtime loader honors `plugins.enabled` on every Hermes version. **Never patch Hermes's own source to work around this.**
     ```bash
     hermes plugins enable band 2>/dev/null && hermes plugins list | grep -qw band \
       || "$HERMES_PY" -c "from hermes_cli import plugins_cmd as C; s=C._get_enabled_set(); s.add('band'); C._save_enabled_set(s); print('enabled band via config')"
     ```

4. Verify the local install with `scripts/verify_install.py`.
   - If `sdk_importable` is false, install `band-sdk>=1.0.0,<2.0.0` into the gateway environment.
   - If `plugin_enabled` is false, repeat step 3.
   - If credential checks are false, continue to credential collection.

5. Ensure agent credentials are present (`BAND_AGENT_ID` + `BAND_API_KEY` in Hermes's `.env`). `scripts/verify_install.py` reports whether they are.
   - Already saved (e.g. the bootstrapper registered the agent before handing off): continue.
   - Pre-created agent: have the user create one at `app.band.ai/agents/new` and save `BAND_AGENT_ID` + `BAND_API_KEY` with Hermes's env writer.
   - Auto-register from a user key — a **script step, not an LLM step**: with `BAND_USER_API_KEY` set in a plain shell, `scripts/register_agent.py` reads it from the environment, mints the agent, and saves only the agent-scoped `BAND_AGENT_ID` + `BAND_API_KEY`. It never prints or persists the user key. **Do not put `BAND_USER_API_KEY` into the agent's own environment or read it yourself** — let the bootstrapper or a plain shell consume it before/outside the agent, so the user key never reaches the LLM. Have the user remove `BAND_USER_API_KEY` afterward.

6. Restart the gateway.
   - Use the user's normal Hermes gateway restart command.
   - On first connect the adapter resolves the owner, creates the Hermes Hub room, writes `BAND_HUB_ROOM`, and wires the hub as the home channel.

7. Verify the gateway with `scripts/verify_gateway.py`.
   - Success means the hub exists, gateway logs show Band connection signals, and no known hub failure signal appears in recent logs.
   - If the owner is unresolved, have the user set `BAND_OWNER_ID` and restart.
   - If no Band log signals appear, re-run install verification and confirm the gateway process is using the expected Python environment.

8. Ask the user to complete the Band UI loop.
   - They should open the Hermes Agent Hub room, @mention the agent, and confirm the agent replies.
   - Remind them that Band has no DMs; an unmentioned message is ignored by design.

9. Optionally enable Band action tools.
   - Add the `band` toolset to each platform that should act on Band in `platform_toolsets` (`~/.hermes/config.yaml`) — **including the `band` platform itself**, or Band sessions get the messaging channel but none of the action tools.
   - Keep `hermes-band` for the messaging channel and add `band` only where action tools are needed.
   - Mutating tools remain owner-gated by the plugin.

## Pitfalls

- Installing into a different Python than the gateway's: `import hermes_band_platform` and `hermes plugins list` can look fine from the repo directory (cwd is on `sys.path`), yet the running gateway never discovers it. Always install with `--python "$HERMES_PY"` and confirm from a neutral directory.
- Patching Hermes's own source to make `hermes plugins enable`/`list` show an entry-point plugin: unnecessary and fragile (it breaks on the next Hermes upgrade and is absent on pip/Docker installs). Use the `plugins.enabled` config fallback instead — the runtime loader honors it regardless.
- Installing the pip package but forgetting `hermes plugins enable band` leaves the entry point discovered but inactive.
- Installing a directory plugin without `band-sdk` in the gateway environment makes the platform unavailable at runtime.
- Saving `BAND_AGENT_ID` with generic config-setting commands can route it to config YAML instead of Hermes `.env`; use the setup wizard or Hermes env writer.
- Treating a successful WebSocket connect as full setup is incomplete; the hub must also be created and persisted as `BAND_HUB_ROOM`.
- Leaving `BAND_USER_API_KEY` in the environment after registration unnecessarily keeps a broad user credential live.
- Exposing the Band *user* key to the LLM: registration is a script step — `register_agent.py` reads `BAND_USER_API_KEY` from the environment and stores only agent-scoped credentials. Don't set the user key in the *agent's* own environment or echo it; run registration in the bootstrapper or a plain shell before the agent takes over.
- Setting `BAND_HOME_ROOM` to another room means cron and notifications use that room instead of the hub.

## Verification

- `scripts/verify_install.py` reports package or directory plugin presence, SDK importability, plugin enablement, and required credential presence.
- `scripts/verify_gateway.py` reports `BAND_HUB_ROOM`, recent Band gateway success signals, and known failure signals.
- The user confirms the Hermes Agent Hub room exists in Band and an @mention test message round-trips.
