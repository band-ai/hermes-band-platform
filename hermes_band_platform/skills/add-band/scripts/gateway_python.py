#!/usr/bin/env python3
"""Resolve and validate the Python interpreter that runs the Hermes gateway.

The Band plugin **must** be installed into the *same* interpreter that runs
``hermes``. Installing into any other environment leaves it undiscoverable even
though ``import hermes_band_platform`` may succeed from the repo directory (cwd
is on ``sys.path``) and ``hermes plugins list`` may look fine. That is the #1
silent install failure, so this script turns the pitfall into a guard: it
resolves the gateway interpreter through a fallback chain and refuses to proceed
unless that interpreter can both *import* ``hermes_cli.config`` and run a
supported Python (3.11–3.13; ``band-sdk`` has no 3.14 wheels yet).

Two properties matter and are easy to get wrong:

* **The probe must not inherit the caller's import path.** ``python -c`` puts cwd
  on ``sys.path`` and honors ``PYTHONPATH``, so *any* interpreter looks like the
  gateway's when this runs from a Hermes source tree. Probes therefore run with
  ``-E`` from an empty working directory.
* **Finding the name is not enough.** ``find_spec('hermes_cli')`` succeeds for a
  source tree whose dependencies were never installed; the failure then surfaces
  much later as ``ModuleNotFoundError: yaml`` inside a helper. The probe performs
  the real ``import hermes_cli.config`` that the helpers depend on.

Set ``HERMES_PY`` (or ``HERMES_PYTHON``) to skip detection. An override that
fails validation is a hard error — it is never silently replaced by a guess.

Modes:
  (default)   emit JSON ``{ok, python, version, method, candidates, error}``
  --print     print only the resolved interpreter path (for ``$(...)`` capture);
              exit non-zero with the reason on stderr if it can't be validated.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, NamedTuple, Optional

MIN_VERSION = (3, 11)
MAX_VERSION = (3, 13)  # inclusive; band-sdk has no 3.14 wheels yet

OVERRIDE_VARS = ("HERMES_PY", "HERMES_PYTHON")

# Venv layouts seen in the wild under a Hermes project directory. `hermes --version`
# reports the project, not the interpreter, and the layout is not fixed: uv's
# `.venv`, a classic `venv`, and FHS-style installs with `bin/` at the root.
_VENV_RELATIVE_PYTHONS = (
    (".venv", "bin", "python"),
    (".venv", "bin", "python3"),
    ("venv", "bin", "python"),
    ("venv", "bin", "python3"),
    ("bin", "python"),
    ("bin", "python3"),
    (".venv", "Scripts", "python.exe"),
    ("venv", "Scripts", "python.exe"),
    ("Scripts", "python.exe"),
)

# Anything a module prints while importing would corrupt a bare JSON payload, so
# the report is delimited and parsed from the marker onward.
_MARKER = "__GATEWAY_PYTHON__"

_PROBE = f"""
import json, sys
info = {{
    "version": list(sys.version_info[:3]),
    "prefix": sys.prefix,
    "base_prefix": getattr(sys, "base_prefix", sys.prefix),
    "executable": sys.executable,
    "hermes_cli": False,
    "origin": "",
    "import_error": "",
}}
try:
    # A real import, not find_spec: this is exactly what the setup helpers do, so
    # a source tree with uninstalled dependencies fails *here*, not later.
    import hermes_cli.config as _config
    info["hermes_cli"] = True
    info["origin"] = getattr(_config, "__file__", "") or ""
except Exception as exc:
    info["import_error"] = "%s: %s" % (type(exc).__name__, exc)
roots = [info["prefix"], info["base_prefix"]]
try:
    import site
    roots.append(site.getusersitepackages())
except Exception:
    pass
_origin = info["origin"]
info["origin_in_prefix"] = bool(_origin) and any(
    _origin.startswith(root) for root in roots if root
)
sys.stdout.write("{_MARKER}" + json.dumps(info))
"""


class Probe(NamedTuple):
    """What a candidate interpreter reported about itself."""

    version: Optional[tuple[int, int, int]]
    has_hermes_cli: bool
    prefix: str = ""
    base_prefix: str = ""
    origin: str = ""
    origin_in_prefix: bool = False
    import_error: str = ""

    @property
    def is_venv(self) -> bool:
        return bool(self.prefix) and self.prefix != self.base_prefix

    def as_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "version": ".".join(str(part) for part in self.version) if self.version else None,
            "has_hermes_cli": self.has_hermes_cli,
            "is_venv": self.is_venv,
        }
        if self.origin:
            data["origin"] = self.origin
            data["origin_in_prefix"] = self.origin_in_prefix
        if self.import_error:
            data["import_error"] = self.import_error
        return data


def _probe(python: str) -> Optional[Probe]:
    """Report on ``python``, or None if it could not be run at all.

    Runs with ``-E`` (ignore ``PYTHON*`` env vars, notably ``PYTHONPATH``) from an
    empty working directory, so what it can import reflects that interpreter's own
    environment rather than the caller's.
    """
    try:
        with tempfile.TemporaryDirectory(prefix="gateway-python-") as neutral_cwd:
            out = subprocess.run(
                [python, "-E", "-c", _PROBE],
                capture_output=True,
                text=True,
                timeout=15,
                cwd=neutral_cwd,
            )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    _, _, payload = out.stdout.partition(_MARKER)
    if not payload:
        return None
    try:
        info = json.loads(payload)
    except json.JSONDecodeError:
        return None
    try:
        version = tuple(int(part) for part in info["version"])
    except (KeyError, TypeError, ValueError):
        return None
    if len(version) != 3:
        return None
    return Probe(
        version=version,  # type: ignore[arg-type]
        has_hermes_cli=bool(info.get("hermes_cli")),
        prefix=str(info.get("prefix") or ""),
        base_prefix=str(info.get("base_prefix") or ""),
        origin=str(info.get("origin") or ""),
        origin_in_prefix=bool(info.get("origin_in_prefix")),
        import_error=str(info.get("import_error") or ""),
    )


def _from_override() -> Optional[str]:
    """An explicitly configured interpreter (``HERMES_PY`` / ``HERMES_PYTHON``)."""
    for var in OVERRIDE_VARS:
        value = os.environ.get(var, "").strip()
        if value:
            return value
    return None


def _hermes_launcher() -> Optional[str]:
    return shutil.which("hermes")


def _from_launcher_sibling() -> list[str]:
    """The interpreter next to the ``hermes`` console script.

    A console script installed into a venv sits beside that venv's ``python``,
    which makes this the most direct signal available — and unlike the project
    path, it holds for every venv directory name.
    """
    hermes = _hermes_launcher()
    if not hermes:
        return []
    try:
        bindir = Path(hermes).resolve().parent
    except OSError:
        return []
    return [str(bindir / name) for name in ("python3", "python", "python.exe")]


def _from_version_banner() -> list[str]:
    """Parse ``hermes --version`` → ``Project: <path>`` → the venv layouts under it."""
    hermes = _hermes_launcher()
    if not hermes:
        return []
    try:
        out = subprocess.run(
            [hermes, "--version"], capture_output=True, text=True, timeout=15
        )
    except (OSError, subprocess.SubprocessError):
        return []
    for line in (out.stdout + out.stderr).splitlines():
        line = line.strip()
        if line.startswith("Project:"):
            project = line.split(":", 1)[1].strip()
            if project:
                root = Path(project)
                return [str(root.joinpath(*parts)) for parts in _VENV_RELATIVE_PYTHONS]
    return []


def _from_launcher_shebang() -> Optional[str]:
    """Read the ``hermes`` launcher's shebang interpreter, if it's a real path."""
    hermes = _hermes_launcher()
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


def _version_error(vstr: str) -> str:
    return (
        f"Gateway Python is {vstr}; Band requires "
        f"{'.'.join(map(str, MIN_VERSION))}–{'.'.join(map(str, MAX_VERSION))} "
        "(band-sdk has no 3.14 wheels yet)"
    )


def _rejection(python: str, probed: Optional[Probe]) -> str:
    """Say why a candidate is unusable, in terms the operator can act on."""
    if probed is None:
        return f"{python} could not be run"
    if not probed.has_hermes_cli:
        return (
            f"{python} cannot import hermes_cli.config "
            f"({probed.import_error or 'hermes_cli is not importable'})"
        )
    return _version_error(".".join(str(part) for part in probed.version or ()))


def resolve() -> dict[str, Any]:
    """Resolve the gateway interpreter, validating import + version."""
    candidates: list[tuple[str, str]] = []  # (path, method)
    seen: set[str] = set()

    def add(path: Optional[str], method: str, *, require_exists: bool = False) -> None:
        if not path:
            return
        if require_exists and not Path(path).exists():
            return
        try:
            real = str(Path(path).resolve())
        except OSError:
            real = path
        if real in seen:
            return
        seen.add(real)
        candidates.append((path, method))

    add(_from_override(), "env")
    for sibling in _from_launcher_sibling():
        add(sibling, "launcher-sibling", require_exists=True)
    for banner_python in _from_version_banner():
        add(banner_python, "version-banner", require_exists=True)
    add(_from_launcher_shebang(), "launcher-shebang")
    for proc_py in _from_running_process():
        add(proc_py, "running-process")
    add(sys.executable, "self")

    tried: list[dict[str, Any]] = []
    for path, method in candidates:
        probed = _probe(path)
        record: dict[str, Any] = {"python": path, "method": method}
        record.update(probed.as_dict() if probed else {"usable": False})
        tried.append(record)

        if probed is not None and probed.has_hermes_cli and probed.version is not None:
            vstr = ".".join(str(part) for part in probed.version)
            in_range = MIN_VERSION <= probed.version[:2] <= MAX_VERSION
            return {
                "ok": in_range,
                "python": path,
                "version": vstr,
                "method": method,
                "candidates": tried,
                "error": None if in_range else _version_error(vstr),
            }

        # An explicit override is a statement of fact by the operator. Falling
        # through to a guess here would reintroduce exactly the silent
        # wrong-interpreter install this script exists to prevent.
        if method == "env":
            var = next(
                (v for v in OVERRIDE_VARS if os.environ.get(v, "").strip() == path),
                OVERRIDE_VARS[0],
            )
            return {
                "ok": False,
                "python": None,
                "version": None,
                "method": None,
                "candidates": tried,
                "error": (
                    f"{var} points at an interpreter that is not the gateway's: "
                    f"{_rejection(path, probed)}. Fix {var} or unset it to auto-detect."
                ),
            }

    detail = "; ".join(
        f"{entry['python']} ({entry.get('import_error') or 'not usable'})" for entry in tried
    )
    return {
        "ok": False,
        "python": None,
        "version": None,
        "method": None,
        "candidates": tried,
        "error": (
            "Could not locate the Python that runs the Hermes gateway — none of the "
            f"candidates could import hermes_cli.config [{detail or 'no candidates'}]. "
            "Set HERMES_PY to the gateway's interpreter and re-run."
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
