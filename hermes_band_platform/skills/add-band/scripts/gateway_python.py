#!/usr/bin/env python3
"""Resolve and validate the Python interpreter that runs the Hermes gateway.

The Band plugin **must** be installed into the *same* interpreter that runs
``hermes``. Installing into any other environment leaves it undiscoverable even
though ``import hermes_band_platform`` may succeed from the repo directory (cwd
is on ``sys.path``) and ``hermes plugins list`` may look fine. That is the #1
silent install failure, so this script turns the pitfall into a guard: it
resolves the gateway interpreter through a fallback chain and refuses to proceed
unless that interpreter can both ``import hermes_cli`` and run a supported
Python (3.11–3.13; ``band-sdk`` has no 3.14 wheels yet).

Modes:
  (default)   emit JSON ``{ok, python, version, method, candidates, error}``
  --print     print only the resolved interpreter path (for ``$(...)`` capture);
              exit non-zero with the reason on stderr if it can't be validated.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

MIN_VERSION = (3, 11)
MAX_VERSION = (3, 13)  # inclusive; band-sdk has no 3.14 wheels yet

# Probe a candidate interpreter: print "<major>.<minor>.<micro> <0|1>" where the
# flag is whether hermes_cli is importable *in that interpreter*.
_PROBE = (
    "import sys, importlib.util;"
    "sys.stdout.write('%d.%d.%d ' % sys.version_info[:3]);"
    "sys.stdout.write('1' if importlib.util.find_spec('hermes_cli') else '0')"
)


def _probe(python: str) -> Optional[tuple[tuple[int, int, int], bool]]:
    """Return ``((major, minor, micro), has_hermes_cli)`` for ``python``, or None."""
    try:
        out = subprocess.run(
            [python, "-c", _PROBE],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    parts = out.stdout.strip().split()
    if len(parts) != 2:
        return None
    try:
        version = tuple(int(x) for x in parts[0].split("."))
    except ValueError:
        return None
    if len(version) != 3:
        return None
    return version, parts[1] == "1"  # type: ignore[return-value]


def _from_version_banner() -> Optional[str]:
    """Parse ``hermes --version`` → ``Project: <path>`` → ``<path>/venv/bin/python``."""
    hermes = shutil.which("hermes")
    if not hermes:
        return None
    try:
        out = subprocess.run(
            [hermes, "--version"], capture_output=True, text=True, timeout=15
        )
    except (OSError, subprocess.SubprocessError):
        return None
    for line in (out.stdout + out.stderr).splitlines():
        line = line.strip()
        if line.startswith("Project:"):
            project = line.split(":", 1)[1].strip()
            if project:
                return str(Path(project) / "venv" / "bin" / "python")
    return None


def _from_launcher_shebang() -> Optional[str]:
    """Read the ``hermes`` launcher's shebang interpreter, if it's a real path."""
    hermes = shutil.which("hermes")
    if not hermes:
        return None
    try:
        with open(hermes, "rb") as handle:
            first = handle.readline(256).decode("utf-8", "replace").strip()
    except OSError:
        return None
    if first.startswith("#!"):
        rest = first[2:].strip().split()
        if rest and rest[0].startswith("/") and "python" in rest[0]:
            return rest[0]
    return None


def _from_running_process() -> list[str]:
    """Best-effort: interpreters of running ``hermes`` processes (Linux ``/proc``)."""
    found: list[str] = []
    try:
        out = subprocess.run(
            ["pgrep", "-f", "hermes"], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return found
    for pid in out.stdout.split():
        try:
            found.append(str(Path(f"/proc/{pid}/exe").resolve()))
        except OSError:
            continue
    return found


def resolve() -> dict[str, Any]:
    """Resolve the gateway interpreter, validating import + version."""
    candidates: list[tuple[str, str]] = []  # (path, method)
    seen: set[str] = set()

    def add(path: Optional[str], method: str) -> None:
        if not path:
            return
        try:
            real = str(Path(path).resolve())
        except OSError:
            real = path
        if real in seen:
            return
        seen.add(real)
        candidates.append((path, method))

    add(_from_version_banner(), "version-banner")
    add(_from_launcher_shebang(), "launcher-shebang")
    for proc_py in _from_running_process():
        add(proc_py, "running-process")
    add(sys.executable, "self")

    tried: list[dict[str, Any]] = []
    for path, method in candidates:
        probed = _probe(path)
        if probed is None:
            tried.append({"python": path, "method": method, "usable": False})
            continue
        version, has_cli = probed
        vstr = ".".join(str(x) for x in version)
        tried.append(
            {"python": path, "method": method, "version": vstr, "has_hermes_cli": has_cli}
        )
        if not has_cli:
            continue
        # The first interpreter that can import hermes_cli *is* the gateway's —
        # gate it on version rather than silently trying a non-gateway python.
        in_range = MIN_VERSION <= version[:2] <= MAX_VERSION
        return {
            "ok": in_range,
            "python": path,
            "version": vstr,
            "method": method,
            "candidates": tried,
            "error": None
            if in_range
            else (
                f"Gateway Python is {vstr}; Band requires "
                f"{'.'.join(map(str, MIN_VERSION))}–{'.'.join(map(str, MAX_VERSION))} "
                "(band-sdk has no 3.14 wheels yet)"
            ),
        }

    return {
        "ok": False,
        "python": None,
        "version": None,
        "method": None,
        "candidates": tried,
        "error": (
            "Could not locate the Python that runs the Hermes gateway — none of the "
            "candidates could import hermes_cli. Set HERMES_PY to the gateway's "
            "interpreter manually and re-run."
        ),
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--print",
        action="store_true",
        dest="print_only",
        help="Print only the resolved interpreter path (for $(...) capture).",
    )
    args = parser.parse_args(argv)

    result = resolve()
    if args.print_only:
        if result["ok"] and result["python"]:
            print(result["python"])
            return 0
        sys.stderr.write((result.get("error") or "interpreter not resolved") + "\n")
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
