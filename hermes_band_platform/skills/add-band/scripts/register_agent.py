#!/usr/bin/env python3
"""Register a Band external agent and persist agent-scoped credentials.

The user-level Band key is read from an environment variable and is never
printed or persisted. Only the returned agent id and agent API key are stored
through Hermes's env writer.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

DEFAULT_BASE_URL = "https://app.band.ai"
DEFAULT_NAME = "Hermes"
DEFAULT_DESCRIPTION = "Hermes AI gateway agent"


class SetupError(RuntimeError):
    """User-facing setup failure."""


def _clean_base_url(base_url: str | None) -> str:
    raw = (base_url or DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL
    if "://" not in raw:
        raw = f"https://{raw}"
    return raw.rstrip("/")


def _extract(mapping: dict[str, Any], *path: str) -> Any:
    cur: Any = mapping
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def extract_agent_credentials(payload: dict[str, Any]) -> tuple[str, str]:
    """Return ``(agent_id, agent_api_key)`` from supported Band responses."""
    agent_id = (
        _extract(payload, "data", "agent", "id")
        or _extract(payload, "agent", "id")
        or _extract(payload, "data", "id")
        or payload.get("agent_id")
        or payload.get("id")
    )
    api_key = (
        _extract(payload, "data", "credentials", "api_key")
        or _extract(payload, "credentials", "api_key")
        or _extract(payload, "data", "api_key")
        or payload.get("api_key")
        or payload.get("key")
        or payload.get("token")
    )
    agent_id = str(agent_id or "").strip()
    api_key = str(api_key or "").strip()
    if not agent_id or not api_key:
        raise SetupError("Band response did not include agent id and agent API key")
    return agent_id, api_key


def register_agent(
    user_api_key: str,
    *,
    base_url: str | None = None,
    name: str = DEFAULT_NAME,
    description: str = DEFAULT_DESCRIPTION,
    timeout: float = 30.0,
) -> tuple[str, str]:
    """Create a Band external agent and return agent-scoped credentials."""
    key = (user_api_key or "").strip()
    if not key:
        raise SetupError("Missing user API key")
    url = f"{_clean_base_url(base_url)}/api/v1/me/agents/register"
    body = json.dumps(
        {
            "agent": {
                "name": name.strip() or DEFAULT_NAME,
                "description": description.strip() or DEFAULT_DESCRIPTION,
            }
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-API-Key": key,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")
        except Exception:
            detail = str(exc)
        # Truncate the raw server body before surfacing it — we don't control
        # its contents, and it ends up in the skill's structured output stream.
        detail = detail.strip()
        if len(detail) > 500:
            detail = detail[:500] + "… (truncated)"
        raise SetupError(f"Band registration failed with HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise SetupError(f"Band registration request failed: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise SetupError("Band registration response was not valid JSON") from exc
    return extract_agent_credentials(payload)


def save_agent_credentials(agent_id: str, agent_api_key: str) -> None:
    """Persist only agent-scoped credentials to Hermes's .env."""
    from hermes_cli.config import save_env_value

    save_env_value("BAND_AGENT_ID", agent_id)
    save_env_value("BAND_API_KEY", agent_api_key)


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.getenv("BAND_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--name", default=DEFAULT_NAME)
    parser.add_argument("--description", default=DEFAULT_DESCRIPTION)
    parser.add_argument("--user-api-key-env", default="BAND_USER_API_KEY")
    parser.add_argument("--no-save", action="store_true", help="Print result without writing .env")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be sent without network")
    args = parser.parse_args(argv)

    if args.dry_run:
        _emit(
            {
                "success": True,
                "dry_run": True,
                "url": f"{_clean_base_url(args.base_url)}/api/v1/me/agents/register",
                "would_read_secret_from": args.user_api_key_env,
                "would_save": [] if args.no_save else ["BAND_AGENT_ID", "BAND_API_KEY"],
            }
        )
        return 0

    user_api_key = os.getenv(args.user_api_key_env, "").strip()
    if not user_api_key:
        _emit(
            {
                "success": False,
                "error": f"Set {args.user_api_key_env} to a Band user API key first",
            }
        )
        return 2

    try:
        agent_id, agent_api_key = register_agent(
            user_api_key,
            base_url=args.base_url,
            name=args.name,
            description=args.description,
        )
        if not args.no_save:
            save_agent_credentials(agent_id, agent_api_key)
        _emit(
            {
                "success": True,
                "agent_id": agent_id,
                "saved": [] if args.no_save else ["BAND_AGENT_ID", "BAND_API_KEY"],
            }
        )
        return 0
    except SetupError as exc:
        _emit({"success": False, "error": str(exc)})
        return 1


if __name__ == "__main__":
    sys.exit(main())
