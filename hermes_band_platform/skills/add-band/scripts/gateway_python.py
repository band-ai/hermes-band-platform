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
  (default)   emit JSON
              ``{ok, python, version, method, is_venv, candidates, warnings, error}``
  --print     print only the resolved interpreter path (for ``$(...)`` capture);
              warnings and, if it can't be validated, the reason go to stderr.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, NamedTuple, Optional

MIN_VERSION = (3, 11)
MAX_VERSION = (3, 13)  # inclusive; band-sdk has no 3.14 wheels yet

OVERRIDE_VARS = ("HERMES_PY", "HERMES_PYTHON")

# The ``kind`` the gateway stamps into ``$HERMES_HOME/gateway.pid``.
_GATEWAY_PID_KIND = "hermes-gateway"

# Methods whose evidence is the *running* gateway rather than this shell's PATH.
_PROCESS_METHODS = ("pid-file", "running-process")

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


def _shebang_interpreter(script: str) -> Optional[str]:
    """The interpreter named in ``script``'s shebang, if it is a real path.

    A console script installed into a venv carries that venv's interpreter on line
    one, which is how a non-Python argv[0] still yields an interpreter.
    """
    try:
        with open(script, "rb") as handle:
            first = handle.readline(256).decode("utf-8", "replace").strip()
    except OSError:
        return None
    if first.startswith("#!"):
        rest = first[2:].strip().split()
        if rest and rest[0].startswith("/") and "python" in rest[0]:
            return rest[0]
    return None


def _from_launcher_shebang() -> Optional[str]:
    """Read the ``hermes`` launcher's shebang interpreter, if it's a real path."""
    hermes = _hermes_launcher()
    return _shebang_interpreter(hermes) if hermes else None


# The shapes a real gateway is launched in. This mirrors the host's own
# tokenizing matcher (``gateway/status.py::_gateway_command_subcommand``), which
# cannot be reused directly: it is a function inside the interpreter this script
# exists to find. Covered:
#
#   <python> -m hermes_cli.main [-p <name>] gateway run      (service units)
#   <python> <venv>/bin/hermes --profile=<name> gateway run  (manual/tmux/nohup)
#   <python> <site-packages>/gateway/run.py                  (runtime entry point)
#   hermes-gateway[.exe]                                     (dedicated launcher)
#
# ``hermes -p <profile> gateway run --replace`` is what the host writes into every
# non-default profile's service unit (``hermes_cli/service_manager.py:705``), so
# not matching ``-p``/``--profile=`` means finding no gateway at all whenever a
# profile is in use. A bare ``gateway`` defaults to ``run``, hence ``( +run|$)`` —
# which still rejects the management subcommands (``gateway status``, ``stop``,
# ``restart``). Matching a loose "hermes" substring instead would match this
# resolver's own path (it lives under ``~/.hermes/``), a ``tail -f
# ~/.hermes/logs/gateway.log``, an ``ssh hermes-server`` tunnel, and the
# bootstrap shell. POSIX ERE only — no GNU shorthand, no inline flags — because
# BSD ``pgrep`` has to accept the identical pattern.
_PROFILE_SELECTOR = r"(--profile|-p)( +|=)[A-Za-z0-9][A-Za-z0-9_.-]*"

_GATEWAY_CMDLINE_RE = re.compile(
    # Entry points that *are* the gateway — no subcommand follows them.
    r"((^|/)gateway/run\.py"
    r"|(^|/)hermes-gateway(\.exe)?( |$)"
    # …and the `hermes … gateway [run]` dispatch. Profile selectors may sit
    # between the launcher and the subcommand (the host strips them before
    # argparse), and a profile may legally be *named* "gateway".
    r"|(hermes_cli\.main|hermes_cli/main\.py|(^|/)hermes)"
    r"( +" + _PROFILE_SELECTOR + r")* +gateway( +run|$))"
)


def _pgrep_gateway_pids() -> list[int]:
    """PIDs whose full argv looks like an actual gateway launch."""
    try:
        out = subprocess.run(
            ["pgrep", "-f", _GATEWAY_CMDLINE_RE.pattern],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    pids: list[int] = []
    for token in out.stdout.split():
        try:
            pids.append(int(token))
        except ValueError:
            continue
    return pids


def _proc_cmdline_path(pid: int) -> Path:
    return Path(f"/proc/{pid}/cmdline")


def _argv0_from_proc(pid: int) -> Optional[str]:
    """argv[0] from Linux ``/proc``. The ``exists()`` check is the platform gate."""
    path = _proc_cmdline_path(pid)
    if not path.exists():
        return None
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    first = raw.split(b"\x00")[0]
    return first.decode("utf-8", "replace") if first else None


def _cmdline_from_ps(pid: int) -> Optional[str]:
    """The full command line via ``ps`` (macOS/BSD, or any host without ``/proc``)."""
    try:
        out = subprocess.run(
            ["ps", "-o", "args=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip() or None


def _argv0_from_ps(pid: int) -> Optional[str]:
    """argv[0] via ``ps`` (macOS/BSD, or any host without ``/proc``).

    Takes the first whitespace-separated token, so an interpreter path containing
    spaces is not recoverable this way — it simply yields no candidate.
    """
    args = _cmdline_from_ps(pid)
    return args.split(" ", 1)[0] if args else None


def _cmdline_from_proc(pid: int) -> Optional[str]:
    """The full command line from Linux ``/proc``, NUL separators flattened."""
    path = _proc_cmdline_path(pid)
    if not path.exists():
        return None
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    return raw.replace(b"\x00", b" ").decode("utf-8", "replace").strip() or None


def _cmdline_for_pid(pid: int) -> Optional[str]:
    """A process's full command line, for deciding *whether* it is the gateway."""
    return _cmdline_from_proc(pid) or _cmdline_from_ps(pid)


def _argv0_for_pid(pid: int) -> Optional[str]:
    """argv[0] exactly as exec'd, or None.

    Deliberately **not** ``/proc/<pid>/exe`` (nor ``ps -o comm=``): a venv's
    ``bin/python`` is usually a symlink to a base interpreter, and dereferencing it
    hands back that base interpreter — which cannot see the venv's site-packages
    and is therefore never the answer we want. It is also how a gateway running
    ``/opt/hermes/.venv/bin/python3`` gets reported as ``/usr/bin/python3.13``.
    """
    return _argv0_from_proc(pid) or _argv0_from_ps(pid)


def _interpreters_for_argv0(argv0: str) -> list[str]:
    """Interpreter candidates implied by a process's argv[0].

    ``<python> -m hermes_cli.main …`` names the interpreter outright. A console
    script launch (``/opt/hermes/.venv/bin/hermes gateway run``) does not — and
    requiring "python" in the name, as this used to, silently yielded *no*
    candidate for that entire launch shape. The script's shebang and its sibling
    ``python`` both name the venv it was installed into, so derive from those
    instead. Anything that is neither a Python nor a ``hermes*`` launcher gets no
    guess at all: that filter is what keeps unrelated PIDs from contributing junk.
    """
    name = Path(argv0).name
    if "python" in name:
        return [argv0]
    if not name.startswith("hermes"):
        return []
    found: list[str] = []
    shebang = _shebang_interpreter(argv0)
    if shebang:
        found.append(shebang)
    bindir = Path(argv0).parent
    found.extend(str(bindir / candidate) for candidate in ("python3", "python", "python.exe"))
    return found


def _hermes_home() -> Path:
    """The Hermes home whose gateway state this resolver should read.

    Env-or-default on purpose, never ``hermes_cli.config.get_hermes_home()``:
    importing the host requires the very interpreter being identified. This
    mirrors the host's own ``_get_process_hermes_home()``, which exists for the
    related reason that gateway identity files always belong to the home the
    gateway process was launched with.
    """
    explicit = os.environ.get("HERMES_HOME", "").strip()
    return Path(explicit).expanduser() if explicit else Path.home() / ".hermes"


def _gateway_pid_record() -> Optional[dict[str, Any]]:
    """Hermes's own record of the running gateway (``$HERMES_HOME/gateway.pid``).

    The strongest evidence there is: the gateway wrote it about itself, and it is
    scoped to *this* home, so a profile's gateway is never confused with the root
    one. It records ``{pid, kind, argv, start_time}`` — note that ``argv[0]`` is
    the *script* (``…/hermes_cli/main.py``), never the interpreter. So the record
    identifies the process, and the live process supplies the interpreter.
    """
    try:
        raw = (_hermes_home() / "gateway.pid").read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        record = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(record, dict) or not isinstance(record.get("pid"), int):
        return None
    kind = record.get("kind")
    if kind is not None and kind != _GATEWAY_PID_KIND:
        return None
    return record


def _pid_is_live(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True  # alive, just owned by another user (container/system service)
    except OSError:
        return False
    return True


def _from_pid_file() -> list[str]:
    """The interpreter of the gateway Hermes itself says is running.

    Ranked above every PATH-derived source: when ``hermes`` on this PATH belongs
    to one environment while the service runs from another, the launcher is a
    guess and this is a fact. Ranked above ``pgrep`` too, because it is scoped to
    one Hermes home instead of picking arbitrarily among several gateways.
    """
    record = _gateway_pid_record()
    if record is None:
        return []
    pid = int(record["pid"])
    if not _pid_is_live(pid):
        return []
    argv0 = _argv0_for_pid(pid)
    if not argv0:
        return []
    # PID-reuse guard: the recorded PID may since have been recycled by something
    # unrelated. Compare against the *recorded* argv rather than
    # ``_GATEWAY_CMDLINE_RE``, so a launch shape this pattern doesn't know about
    # still validates — that record came from the gateway itself.
    cmdline = _cmdline_for_pid(pid) or ""
    argv = record.get("argv")
    tail = " ".join(str(part) for part in argv[1:]) if isinstance(argv, list) else ""
    if tail:
        if tail not in cmdline:
            return []
    elif not _GATEWAY_CMDLINE_RE.search(cmdline):
        return []
    return _interpreters_for_argv0(argv0)


def _from_running_process() -> list[str]:
    """Interpreters of running gateway processes, found by scanning.

    The fallback for when there is no readable pid record — a gateway in another
    Hermes home, in a container, or owned by another user. It stays *below* the
    launcher sources because a match here is unscoped: with several gateways
    running, which one is found first is arbitrary.
    """
    exclude = {os.getpid(), os.getppid()}
    found: list[str] = []
    for pid in _pgrep_gateway_pids():
        if pid in exclude:
            continue
        argv0 = _argv0_for_pid(pid)
        if argv0:
            found.extend(_interpreters_for_argv0(argv0))
    return found


def _same_path(left: str, right: str) -> bool:
    """Whether two candidate paths name the same interpreter (symlinks included)."""
    try:
        return Path(left).resolve() == Path(right).resolve()
    except OSError:
        return left == right


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

    # Order is evidence, strongest first. The operator's own statement, then the
    # gateway's record of itself, then what this shell's PATH implies, then a
    # scan, then this interpreter. Everything below `pid-file` describes an
    # environment that *may* be the gateway's; `pid-file` describes the one that
    # is.
    add(_from_override(), "env")
    for pid_file_python in _from_pid_file():
        add(pid_file_python, "pid-file", require_exists=True)
    for sibling in _from_launcher_sibling():
        add(sibling, "launcher-sibling", require_exists=True)
    for banner_python in _from_version_banner():
        add(banner_python, "version-banner", require_exists=True)
    add(_from_launcher_shebang(), "launcher-shebang")
    for proc_py in _from_running_process():
        add(proc_py, "running-process", require_exists=True)
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
            # Shape is a nudge, never a verdict. A system-wide Hermes (distro
            # package, `pip --break-system-packages`, single-python container) is a
            # legitimate install, and which candidate *wins* stays a question of
            # evidence — the method order — not of looks. An explicit override is
            # the operator's own statement, so it is never second-guessed here.
            warnings: list[str] = []
            if in_range and method != "env" and not probed.is_venv:
                warnings.append(
                    f"{path} looks like a system Python (sys.prefix == sys.base_prefix), "
                    "not a virtualenv. If Hermes actually runs from a project venv, set "
                    "HERMES_PY to point at it."
                )
            # A PATH-derived winner while the live gateway points somewhere else is
            # the wrong-interpreter bug in the making. It can only get here when the
            # process evidence was unusable or unscoped (a gateway in another home,
            # so it stays ranked below) — either way, say so instead of installing
            # quietly into the wrong environment.
            if in_range and method != "env" and method not in _PROCESS_METHODS:
                disagreeing = [
                    candidate
                    for candidate, candidate_method in candidates
                    if candidate_method in _PROCESS_METHODS and not _same_path(candidate, path)
                ]
                if disagreeing:
                    warnings.append(
                        f"{path} came from {method}, but a running gateway reports "
                        f"{disagreeing[0]}. This shell's PATH and the live process "
                        f"disagree; if the process is right, set HERMES_PY={disagreeing[0]}."
                    )
            return {
                "ok": in_range,
                "python": path,
                "version": vstr,
                "method": method,
                "is_venv": probed.is_venv,
                "candidates": tried,
                "warnings": warnings,
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
                "is_venv": None,
                "candidates": tried,
                "warnings": [],
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
        "is_venv": None,
        "candidates": tried,
        "warnings": [],
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
            # stdout stays path-only — callers capture it with `$(...)`.
            for warning in result.get("warnings") or ():
                sys.stderr.write(f"warning: {warning}\n")
            print(result["python"])
            return 0
        sys.stderr.write((result.get("error") or "interpreter not resolved") + "\n")
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
