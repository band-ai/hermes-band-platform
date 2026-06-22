#!/usr/bin/env python3
"""Register a Band external agent and save Hermes agent-scoped credentials.

Temporary helper until ``band-sdk`` publishes ``band.cli.register_agent``. The
Band *user* API key is read from ``BAND_API_KEY`` (the name the Band web app's
onboarding snippet exports) or ``BAND_USER_API_KEY``, and is never printed. Only
the returned agent-scoped ``BAND_AGENT_ID`` + ``BAND_API_KEY`` are persisted to
the gateway ``.env`` through Hermes's env writer — the user key in the shell env
is the caller's to ``unset`` before any agent/LLM handoff (the bootstrap does).

If ``BAND_AGENT_ID`` is already set, registration short-circuits (pass
``--force`` to re-register). This keeps a re-run from minting a second agent and,
because the user key and the persisted agent key share the ``BAND_API_KEY`` name,
from misreading an already-registered agent key as a fresh user key.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

_DEFAULT_REGISTRATION_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)


def _registration_headers(user_key: str) -> dict[str, str]:
    """Return headers for Band's agent-registration endpoint.

    The registration endpoint sits behind Cloudflare. In practice it can reject
    sparse script/client fingerprints with HTTP 403 / error 1010 even when the
    Band API key is valid, so keep a browser-like request shape here and in the
    future SDK CLI replacement.
    """
    return {
        "User-Agent": os.environ.get("BAND_USER_AGENT", _DEFAULT_REGISTRATION_USER_AGENT),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Content-Type": "application/json",
        "X-API-Key": user_key,
    }


def _nested(data: dict[str, Any], *path: str) -> Any:
    cur: Any = data
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _extract_credentials(data: dict[str, Any]) -> tuple[str, str]:
    agent_id = (
        _nested(data, "data", "agent", "id")
        or _nested(data, "agent", "id")
        or _nested(data, "data", "id")
        or data.get("agent_id")
        or data.get("id")
        or ""
    )
    api_key = (
        _nested(data, "data", "credentials", "api_key")
        or _nested(data, "credentials", "api_key")
        or _nested(data, "data", "api_key")
        or data.get("api_key")
        or data.get("key")
        or data.get("token")
        or ""
    )
    return str(agent_id).strip(), str(api_key).strip()


def _save_credentials(agent_id: str, api_key: str) -> None:
    try:
        from hermes_cli.config import save_env_value
    except Exception as exc:  # pragma: no cover - environment failure path
        raise RuntimeError(
            "Could not import hermes_cli.config.save_env_value from this Python. "
            "Run this helper with the Hermes gateway Python."
        ) from exc

    save_env_value("BAND_AGENT_ID", agent_id)
    save_env_value("BAND_API_KEY", api_key)


def register_agent(force: bool = False) -> dict[str, Any]:
    # A prior run already minted an agent. Don't register again — and don't
    # misread the persisted agent key (also BAND_API_KEY) as a fresh user key.
    existing_agent = os.environ.get("BAND_AGENT_ID", "").strip()
    if existing_agent and not force:
        return {
            "success": True,
            "already_registered": True,
            "agent_id": existing_agent,
            "saved": [],
        }

    # The web app's onboarding snippet exports the *user* key as BAND_API_KEY;
    # the manual/docs path may name it BAND_USER_API_KEY. Accept either,
    # preferring the explicit user-key name.
    user_key = (
        os.environ.get("BAND_USER_API_KEY")
        or os.environ.get("BAND_API_KEY")
        or ""
    ).strip()
    if not user_key:
        raise RuntimeError("a Band API key is required (set BAND_API_KEY or BAND_USER_API_KEY)")

    base_url = os.environ.get("BAND_BASE_URL", "https://app.band.ai").rstrip("/")
    name = os.environ.get("BAND_AGENT_NAME", "Hermes Agent")
    description = os.environ.get("BAND_AGENT_DESCRIPTION", "Hermes agent on Band")
    body = json.dumps({"agent": {"name": name, "description": description}}).encode()
    request = urllib.request.Request(
        f"{base_url}/api/v1/me/agents/register",
        data=body,
        method="POST",
        headers=_registration_headers(user_key),
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            response_body = response.read().decode("utf-8")
            status = response.status
    except urllib.error.HTTPError as exc:
        response_body = exc.read().decode("utf-8", "replace")
        raise RuntimeError(
            f"Band registration failed (HTTP {exc.code}): {response_body[:300]}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Band registration failed: {exc.reason}") from exc

    if status not in {200, 201}:
        raise RuntimeError(
            f"Band registration failed (HTTP {status}): {response_body[:300]}"
        )

    try:
        payload = json.loads(response_body)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Band registration response was not valid JSON") from exc

    agent_id, api_key = _extract_credentials(payload)
    if not agent_id or not api_key:
        raise RuntimeError("Band registration response missing agent id/key")

    _save_credentials(agent_id, api_key)
    return {
        "success": True,
        "agent_id": agent_id,
        "saved": ["BAND_AGENT_ID", "BAND_API_KEY"],
    }


def main() -> int:
    force = "--force" in sys.argv[1:]
    try:
        result = register_agent(force=force)
    except Exception as exc:
        print(json.dumps({"success": False, "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
