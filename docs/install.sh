#!/usr/bin/env bash
# Band <-> Hermes installer. Run on the host that runs the Hermes gateway.
#
# Prepares Band credentials, installs the `add-band` skill where Hermes discovers
# user skills, then runs `hermes /add-band` so Hermes' own agent installs/enables
# the plugin, restarts the gateway, and verifies the hub. The setup logic lives in
# the skill (fetched at run time), so this stays thin.
#
# Precondition: Hermes is installed AND already a working agent (model + auth +
# terminal tool). This adds Band to a functioning Hermes; it does not bootstrap
# Hermes from zero.
#
# Two ways to run:
#   • Download then run (prompts work):  curl -fsSL <url> -o install.sh && bash install.sh
#   • Paste into a terminal: set credentials first so it runs non-interactively, e.g.
#       export BAND_AGENT_ID=... BAND_API_KEY=...   # or: export BAND_USER_API_KEY=...
#     (Interactive prompts are unreliable when a multi-line script is pasted directly.)
#
# Optional env: HERMES_PY (gateway python), REPO_URL, REPO_REF (tag/branch), SKILL_SRC.

set -euo pipefail
REPO_URL="${REPO_URL:-https://github.com/band-ai/hermes-band-platform}"
REPO_REF="${REPO_REF:-main}"

# 1. Interpreter that runs hermes (the host CLI is present before the plugin).
command -v hermes >/dev/null 2>&1 || { echo "hermes not on PATH." >&2; exit 1; }
: "${HERMES_PY:=$(hermes --version 2>&1 | sed -n 's/^Project: //p')/venv/bin/python}"
"$HERMES_PY" -c 'import sys,hermes_cli; v=sys.version_info; assert (3,11)<=v<(3,14), v' \
  || { echo "Set HERMES_PY to the python that runs your gateway (needs 3.11-3.13) and re-run." >&2; exit 1; }

# 2. Credentials -> Hermes' .env via its own writer. Prefer env vars (set before
#    running); fall back to prompts only when attached to a real terminal.
save_creds() { AID="$1" AKEY="$2" "$HERMES_PY" -c \
  "import os;from hermes_cli.config import save_env_value as s;s('BAND_AGENT_ID',os.environ['AID']);s('BAND_API_KEY',os.environ['AKEY'])"; }

if [ -n "${BAND_AGENT_ID:-}" ] && [ -n "${BAND_API_KEY:-}" ]; then
  save_creds "$BAND_AGENT_ID" "$BAND_API_KEY"; echo "Saved agent credentials to Hermes .env"
elif [ -n "${BAND_USER_API_KEY:-}" ]; then
  export BAND_USER_API_KEY                       # the skill mints the agent; never persisted
  echo "Will auto-register a Band agent via the skill."
elif [ -t 0 ] || [ -e /dev/tty ]; then
  printf 'Already have a Band agent? [y/N] '; read -r have </dev/tty
  if [ "$have" = y ] || [ "$have" = Y ]; then
    read -rp  "BAND_AGENT_ID: " aid  </dev/tty
    read -rsp "BAND_API_KEY:  " akey </dev/tty; echo
    save_creds "$aid" "$akey"; unset akey
  else
    read -rsp "BAND_USER_API_KEY (to auto-register): " BAND_USER_API_KEY </dev/tty; echo
    export BAND_USER_API_KEY
  fi
else
  echo "No credentials in env and no terminal for prompts." >&2
  echo "Set BAND_AGENT_ID + BAND_API_KEY (or BAND_USER_API_KEY) and re-run." >&2
  exit 1
fi

# 3. Install the add-band skill where Hermes discovers USER skills. Plugin-shipped
#    skills only register after the plugin loads; a skill under ~/.hermes/skills/
#    is indexed at the next session, so `hermes /add-band` works pre-install.
dest="$HOME/.hermes/skills/add-band"; mkdir -p "$dest"
if [ -n "${SKILL_SRC:-}" ]; then
  cp -R "$SKILL_SRC/." "$dest/"
else
  tmp="$(mktemp -d)"
  git clone --depth 1 --branch "$REPO_REF" "$REPO_URL" "$tmp/repo"
  cp -R "$tmp/repo/hermes_band_platform/skills/add-band/." "$dest/"
fi
echo "Installed add-band skill to $dest"

# 4. Hand off to Hermes' own agent (no exec -- returns to your shell when it ends).
echo "Handing off to Hermes; @mention the agent in the 'Hermes Agent Hub' room when it finishes."
hermes /add-band
