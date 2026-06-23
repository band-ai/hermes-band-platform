#!/usr/bin/env python3
"""Generate the repository-root ``plugin.yaml`` from the packaged manifest.

**Single source of truth:** ``hermes_band_platform/plugin.yaml`` — the wheel/pip
manifest, version-managed by release-please. The repository root needs its own
``plugin.yaml`` only so a *git directory install*
(``hermes plugins install band-ai/hermes-band-platform``) exposes the same
manifest as the wheel. Rather than hand-maintain a second copy and guard it
against drift, we **generate** the root file from the packaged one.

  --check   exit non-zero if the root manifest is missing or stale (CI guard)
  --write   (re)generate the root manifest from the packaged manifest

CI runs ``--check`` so a stale root can never merge; the release workflow runs
``--write`` into the release-please PR after it bumps the packaged version, so
the generated root lands atomically with the bump.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / "hermes_band_platform" / "plugin.yaml"
GENERATED = ROOT / "plugin.yaml"

_HEADER = (
    "# GENERATED FILE — do not edit.\n"
    "# Source of truth: hermes_band_platform/plugin.yaml\n"
    "# Regenerate with: python scripts/sync_root_manifest.py --write\n"
)


def expected_root() -> str:
    """The exact contents the generated root manifest should have."""
    return _HEADER + CANONICAL.read_text()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--check", action="store_true", help="Fail if the root manifest is stale or missing."
    )
    group.add_argument(
        "--write", action="store_true", help="Regenerate the root manifest from the packaged one."
    )
    args = parser.parse_args(argv)

    if not CANONICAL.exists():
        sys.stderr.write(f"Canonical manifest missing: {CANONICAL}\n")
        return 2

    want = expected_root()
    have = GENERATED.read_text() if GENERATED.exists() else None

    if args.write:
        if have == want:
            print(f"{GENERATED.name} already in sync with the packaged manifest")
        else:
            GENERATED.write_text(want)
            print(f"Regenerated {GENERATED.name} from the packaged manifest")
        return 0

    # --check
    if have == want:
        print(f"{GENERATED.name} is in sync with the packaged manifest")
        return 0
    sys.stderr.write(
        f"{GENERATED.name} is stale or missing. "
        "Run: python scripts/sync_root_manifest.py --write\n"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
