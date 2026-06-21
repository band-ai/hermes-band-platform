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

Never ask the user to paste a Band user API key into a command line, and never read it yourself. Until the SDK registration CLI is published, the key is consumed by the bundled `scripts/register_agent.py` helper (a small Python helper that reads it from the environment) — ideally by the bootstrapper *before* this skill runs, so it never enters the agent's environment. Only the resulting agent-scoped `BAND_AGENT_ID` + `BAND_API_KEY` are stored; the user key is never printed or persisted. Once `band.cli.register_agent` ships in `band-sdk`, replace this temporary helper with the SDK CLI. Have the user remove `BAND_USER_API_KEY` after the one registration step.

## How to Run

Use the `terminal` tool for commands and the helper scripts shipped with this skill. Run scripts with the same Python interpreter that runs Hermes.

Skill helper paths are relative to this skill directory:

- `scripts/gateway_python.py` — resolve + validate the gateway interpreter
- `scripts/register_agent.py` — temporary registration helper; replace with `band.cli.register_agent` after the SDK CLI is published
- `scripts/verify_install.py`
- `scripts/verify_gateway.py`
- `scripts/verify_roundtrip.py` — prove the agent can post to its owner

Registration temporarily ships as `scripts/register_agent.py` because the SDK CLI is not published yet. The helper saves `BAND_AGENT_ID` + `BAND_API_KEY` through Hermes's env writer and never prints the user key. When `band-register-agent` / `band.cli.register_agent` is available in `band-sdk`, switch registration to the SDK CLI and remove the bundled helper. The setup scripts emit JSON so you can inspect success, missing checks, and next actions without exposing secrets.

This skill is **resumable**: run `verify_install.py` first and act *only* on what's missing, so re-running after a partial failure never double-installs or re-registers.

## Quick Reference

Identify the gateway interpreter first — every install and script call uses it. Use the
resolver rather than hand-rolling it; it fails loud on the wrong/unsupported Python
instead of leaving a silent wrong-venv install:

```bash
HERMES_PY="$(scripts/gateway_python.py --print)" || { scripts/gateway_python.py; exit 1; }
```

Git-ref package install for now (auto-installs `band-sdk`), then enable with a config fallback for builds whose CLI does not list entry-point plugins. The PyPI-switch PR should change this to a pinned `hermes-band-platform==...` install, but its merge is blocked until the package is published and verified on PyPI:

```bash
BAND_HERMES_REF="${BAND_HERMES_REF:-main}"
uv pip install --python "$HERMES_PY" "hermes-band-platform @ git+https://github.com/band-ai/hermes-band-platform.git@${BAND_HERMES_REF}"
hermes plugins enable band 2>/dev/null && hermes plugins list | grep -qw band \
  || "$HERMES_PY" -c "from hermes_cli import plugins_cmd as C; s=C._get_enabled_set(); s.add('band'); C._save_enabled_set(s); print('enabled band via config')"
```
Directory plugin install instead (CLI-native, no entry-point caveat; it does **not** install dependencies, so prompt before installing `band-sdk` separately and show the import-check error if the user declines):

```bash
hermes plugins install band-ai/hermes-band-platform --enable
echo "Directory plugin installs do not install Python dependencies; installing band-sdk into the gateway Python."
uv pip install --python "$HERMES_PY" 'band-sdk>=1.0.0,<2.0.0'
"$HERMES_PY" -c "import band" || { echo "band-sdk is still missing from the gateway Python. Band cannot start until you run: uv pip install --python \"$HERMES_PY\" 'band-sdk>=1.0.0,<2.0.0'" >&2; exit 1; }
```

Register (temporary bundled helper — needs `BAND_USER_API_KEY`), then verify install / gateway / prove a round-trip (always with the gateway interpreter):

```bash
"$HERMES_PY" scripts/register_agent.py  # mints the agent and saves BAND_AGENT_ID + BAND_API_KEY
"$HERMES_PY" scripts/verify_install.py
"$HERMES_PY" scripts/verify_gateway.py
"$HERMES_PY" scripts/verify_roundtrip.py  # connected != working: prove an outbound hub send
```

## Procedure

The flow is **state-driven**: resolve the interpreter once, take stock of what's already
done, then fix only the gaps. This makes a re-run after a partial failure safe — it never
re-installs or re-registers what's already in place.

1. Resolve the gateway interpreter — once, up front; every later step uses `$HERMES_PY`.
   - The plugin must be installed into the *same* interpreter that runs `hermes`, or it
     stays undiscoverable even though `import hermes_band_platform` succeeds from the repo
     directory. Use the resolver, which validates `import hermes_cli` and a supported
     version (3.11–3.13; `band-sdk` has no 3.14 wheels) and **fails loud** rather than
     leaving a silent wrong-venv install:
     ```bash
     HERMES_PY="$(scripts/gateway_python.py --print)" || { scripts/gateway_python.py; exit 1; }
     ```
   - On failure, run `scripts/gateway_python.py` (no `--print`) for the JSON reason
     (wrong version, `hermes_cli` not importable, candidates tried) and resolve that first.

2. Take stock — run `scripts/verify_install.py` with the gateway interpreter and read
   `missing[]`. Do **only** the steps whose checks are missing; skip the rest.
   ```bash
   "$HERMES_PY" scripts/verify_install.py
   ```
   - `package_importable` / `entry_point` false → install (step 3).
   - `sdk_importable` false → install `band-sdk` (step 3 note).
   - `plugin_enabled` false → enable (step 4).
   - `band_agent_id_present` / `band_api_key_present` false → credentials (step 5).
   - All true → jump to restart + verification (steps 6–8).

3. Install the plugin into `$HERMES_PY` (skip if `package_importable` + `entry_point` are already true).
   - Git-ref package install for now (auto-installs `band-sdk`). Leave the production PyPI PR blocked until `hermes-band-platform` is published and verified, then switch this to a pinned PyPI version:
     ```bash
     BAND_HERMES_REF="${BAND_HERMES_REF:-main}"
     uv pip install --python "$HERMES_PY" "hermes-band-platform @ git+https://github.com/band-ai/hermes-band-platform.git@${BAND_HERMES_REF}"
     ```
   - Directory install instead (CLI-native; the SDK is not bundled, so explicitly prompt/install it and fail clearly if it remains absent):
     ```bash
     hermes plugins install band-ai/hermes-band-platform --enable
     echo "Directory plugin installs do not install Python dependencies; installing band-sdk into the gateway Python."
     uv pip install --python "$HERMES_PY" 'band-sdk>=1.0.0,<2.0.0'
     "$HERMES_PY" -c "import band" || { echo "band-sdk is still missing from the gateway Python. Band cannot start until you run: uv pip install --python \"$HERMES_PY\" 'band-sdk>=1.0.0,<2.0.0'" >&2; exit 1; }
     ```
   - For Nix installs, ensure the package and `band-sdk` are in the gateway Python environment and `plugins.enabled` contains `band`.
   - **Assert the install landed in the gateway interpreter:** re-run `"$HERMES_PY" scripts/verify_install.py` and confirm `entry_point` (or `directory_manifest`) is now true. This is exactly where the wrong-interpreter trap surfaces — catch it here, not in a silent runtime miss.

4. Enable the plugin (skip if you used `hermes plugins install … --enable`).
   - Try the CLI; if the build does not list entry-point plugins, write the `plugins.enabled` config directly. The runtime loader honors `plugins.enabled` on every Hermes version. **Never patch Hermes's own source to work around this.**
     ```bash
     hermes plugins enable band 2>/dev/null && hermes plugins list | grep -qw band \
       || "$HERMES_PY" -c "from hermes_cli import plugins_cmd as C; s=C._get_enabled_set(); s.add('band'); C._save_enabled_set(s); print('enabled band via config')"
     ```

5. Ensure agent credentials are present (`BAND_AGENT_ID` + `BAND_API_KEY` in Hermes's `.env`). `scripts/verify_install.py` reports whether they are.
   - Already saved (e.g. the bootstrapper registered the agent before handing off): continue.
   - Pre-created agent: have the user create one at `app.band.ai/agents/new` and save `BAND_AGENT_ID` + `BAND_API_KEY` with Hermes's env writer.
   - Auto-register from a user key — a **script step, not an LLM step**. Until the SDK CLI is published, use the bundled `scripts/register_agent.py` helper. With `BAND_USER_API_KEY` set in a plain shell, the helper reads it from the environment, mints the agent, and saves agent-scoped creds through Hermes's env writer; it never prints or persists the *user* key:
     ```bash
     "$HERMES_PY" scripts/register_agent.py   # saves BAND_AGENT_ID + BAND_API_KEY through Hermes env writer
     unset BAND_USER_API_KEY
     ```
     This is **idempotent at the flow level**: step 2 only routes here when `verify_install` reports the credentials missing, so a re-run never mints a second agent. **Do not put `BAND_USER_API_KEY` into the agent's own environment or read it yourself** — let the bootstrapper or a plain shell consume it before/outside the agent, so the user key never reaches the LLM. After `band.cli.register_agent` is published in `band-sdk`, replace this helper call with `"$HERMES_PY" -m band.cli.register_agent`. Have the user remove `BAND_USER_API_KEY` afterward.

6. Restart the gateway.
   - Use the user's normal Hermes gateway restart command.
   - On first connect the adapter resolves the owner, creates the Hermes Hub room, writes `BAND_HUB_ROOM`, and wires the hub as the home channel.

7. Verify the gateway with `scripts/verify_gateway.py`.
   - Success means the hub exists, gateway logs show Band connection signals, and no known hub failure signal appears in recent logs.
   - If the owner is unresolved, have the user set `BAND_OWNER_ID` and restart.
   - If no Band log signals appear, re-run install verification and confirm the gateway process is using the expected Python environment.

8. **Prove the round-trip** with `scripts/verify_roundtrip.py` — *connected is not working*. `connect()` only opens the socket, and hub bootstrap runs in a `try/except` that never blocks connect, so confirm the agent can actually post to its owner:
   ```bash
   "$HERMES_PY" scripts/verify_roundtrip.py                 # outbound proof (default)
   "$HERMES_PY" scripts/verify_roundtrip.py --await-reply   # full duplex: also wait for the owner's @mention
   ```
   - A successful send exercises auth + room + the exact REST path real replies use. If this fails after `verify_gateway.py` passed, the problem is mentions/room state, not the socket.
   - `--await-reply` additionally posts the check, then waits for the owner to @mention back — proving inbound delivery too. Use it when you want the user to confirm live.

9. **Close the loop — offer the first real room.** The hub is a private owner↔agent control room; the user's actual goal is usually a room with other people. Don't stop at the hub:
   - Offer: *"You're live in the Hub. Want me to create your first room? Tell me who to add."*
   - On yes, use `band_create_room(person=<handle/name>, message=<intro>)` — it resolves → creates → adds → messages in one call and returns `{room_id, added, sent}`.
   - The Band tools are owner-gated and **fail-closed**. From this setup session the caller may not be the resolved Band owner, so a mutation can be refused: if so, either have the user run the request from the Hub in Band (where the owner bypass applies) or add the calling `platform:user_id` to `BAND_TOOL_OWNERS`. On decline, point them at `band_create_room` / the Band UI for later.
   - Remind them Band has no DMs; an unmentioned message is ignored by design.

10. Optionally enable Band action tools (if not already needed for step 9).
   - Add the `band` toolset to each platform that should act on Band in `platform_toolsets` (`~/.hermes/config.yaml`) — **including the `band` platform itself**, or Band sessions get the messaging channel but none of the action tools.
   - Keep `hermes-band` for the messaging channel and add `band` only where action tools are needed.
   - Mutating tools remain owner-gated by the plugin.

## Pitfalls

- Installing into a different Python than the gateway's: `import hermes_band_platform` and `hermes plugins list` can look fine from the repo directory (cwd is on `sys.path`), yet the running gateway never discovers it. Always install with `--python "$HERMES_PY"` and confirm from a neutral directory.
- Patching Hermes's own source to make `hermes plugins enable`/`list` show an entry-point plugin: unnecessary and fragile (it breaks on the next Hermes upgrade and is absent on pip/Docker installs). Use the `plugins.enabled` config fallback instead — the runtime loader honors it regardless.
- Installing the package but forgetting `hermes plugins enable band` leaves the entry point discovered but inactive.
- Installing a directory plugin without `band-sdk` in the gateway environment makes the platform unavailable at runtime.
- Saving `BAND_AGENT_ID` with generic config-setting commands can route it to config YAML instead of Hermes `.env`; use the setup wizard or Hermes env writer.
- Treating a successful WebSocket connect as full setup is incomplete; the hub must also be created and persisted as `BAND_HUB_ROOM`, **and** the agent must be able to post to it — prove the latter with `scripts/verify_roundtrip.py`, since hub bootstrap runs in a `try/except` that never blocks connect.
- Leaving `BAND_USER_API_KEY` in the environment after registration unnecessarily keeps a broad user credential live.
- Exposing the Band *user* key to the LLM: registration is a script step — the temporary bundled `scripts/register_agent.py` helper reads `BAND_USER_API_KEY` from the environment and saves only agent-scoped credentials. Don't set the user key in the *agent's* own environment or echo it; run registration in the bootstrapper or a plain shell before the agent takes over. Replace the helper with the SDK CLI once `band.cli.register_agent` is published.
- Setting `BAND_HOME_ROOM` to another room means cron and notifications use that room instead of the hub.

## Verification

- `scripts/gateway_python.py` resolves and version-gates the gateway interpreter (run before anything else).
- `scripts/verify_install.py` reports package or directory plugin presence, SDK importability, plugin enablement, and required credential presence. Run it as `$HERMES_PY` so its `entry_point` check reflects the gateway interpreter, not whatever shell python you happen to be in.
- `scripts/verify_gateway.py` reports `BAND_HUB_ROOM`, recent Band gateway success signals, and known failure signals.
- `scripts/verify_roundtrip.py` proves the agent can actually post to the hub (and, with `--await-reply`, that the owner's @mention reaches it) — the step that turns "connected" into "working".
- The user confirms the Hermes Agent Hub room exists in Band and an @mention test message round-trips.
