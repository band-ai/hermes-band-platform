"""Locate the plugin tree and the Band SDK from a bundled setup script.

These scripts ship *inside* the plugin, and the plugin lands in one of three
places — so a script must discover where it is rather than assume:

===================  ===============================================
repo checkout        ``<repo>/hermes_band_platform``
pip install          ``<site-packages>/hermes_band_platform``
directory plugin     ``$HERMES_HOME/plugins/band``  ← installer default
===================  ===============================================

The directory-plugin layout is the trap. ``install.sh`` copies the package's
*contents* into ``plugins/band``, so **no directory named
``hermes_band_platform`` exists there** and nothing is importable under that
name. Anchoring on the package *name*, or putting the tree's parent on
``sys.path``, breaks in ways that look unrelated to layout:

* ``sys.path.insert(0, <plugin_root>.parent)`` in the directory layout puts
  ``$HERMES_HOME/plugins`` on the path, which makes ``import band`` resolve to
  the **plugin package** ``plugins/band/`` and shadow the SDK — so
  ``band.client`` stops existing even though ``band-sdk`` is installed.
* ``band-sdk`` may live in ``$HERMES_HOME/band-libs`` (the installer puts it
  there when the gateway venv is read-only). Only the *plugin's* loader shim
  puts that on ``sys.path``, and only at gateway load — a standalone script
  gets nothing, so ``import band`` fails with a correct install.

So: identify the plugin root by **markers, never by name**, never put its parent
on ``sys.path``, and reach the SDK through the plugin's own ``_band_libs`` shim
loaded by path.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from typing import Any, NamedTuple, Optional

# A plugin root is identified by what it contains, not by what it is called.
PLUGIN_MARKERS = ("adapter.py", "plugin.yaml")


class Layout(NamedTuple):
    """Where this script found the plugin, the Hermes home, and the SDK."""

    root: Optional[Path]
    source: Optional[str]
    hermes_home: Optional[Path]
    hermes_home_source: Optional[str]
    band_libs_dir: Optional[Path]
    sdk_origin: Optional[str]
    candidates: list[dict[str, Any]]
    error: Optional[str]

    def as_dict(self) -> dict[str, Any]:
        """The ``layout`` block attached to every payload, pass or fail.

        A *successful* run records which tree and which ``band-libs`` it proved,
        which is what an operator otherwise has to reconstruct by hand.
        """
        return {
            "plugin_root": str(self.root) if self.root else None,
            "source": self.source,
            "hermes_home": str(self.hermes_home) if self.hermes_home else None,
            "hermes_home_source": self.hermes_home_source,
            "band_libs_dir": str(self.band_libs_dir) if self.band_libs_dir else None,
            "band_libs_present": bool(self.band_libs_dir and self.band_libs_dir.is_dir()),
            "sdk_origin": self.sdk_origin,
            "interpreter": sys.executable,
            "script": str(Path(sys.argv[0]).resolve()) if sys.argv and sys.argv[0] else None,
            "candidates": self.candidates,
        }


def is_plugin_root(path: Path) -> bool:
    return all((path / marker).is_file() for marker in PLUGIN_MARKERS)


def _env_home() -> Optional[Path]:
    explicit = os.environ.get("HERMES_HOME", "").strip()
    return Path(explicit).expanduser() if explicit else None


def _host_home() -> Optional[Path]:
    """The Hermes home according to the host itself.

    Hermes has a real profile concept (e.g. ``/opt/data/profiles/<name>``) that a
    plain ``~/.hermes`` default cannot reproduce, so ask it when we can. These
    scripts run under the gateway interpreter, so ``hermes_cli`` is importable by
    construction.
    """
    try:
        from hermes_cli.config import get_hermes_home

        home = get_hermes_home()
        return Path(str(home)).expanduser() if home else None
    except Exception:
        return None


def _hermes_home(root: Optional[Path]) -> tuple[Path, str]:
    """Resolve the Hermes home whose ``band-libs`` belongs to ``root``.

    Order matters. ``$HERMES_HOME`` first: it is explicit operator intent *and*
    the plugin's ``_band_libs`` shim reads only that variable, so agreeing with it
    keeps the SDK path and the printed install command consistent. A
    directory-plugin tree comes next — ``<home>/plugins/band`` names its own home,
    which is stronger evidence about *this tree's* SDK than the host's configured
    home is (they differ when a script is run from another install). The host API
    is the fallback for trees that say nothing, ahead of the bare default.
    """
    explicit = _env_home()
    if explicit is not None:
        return explicit, "env"
    if root is not None and root.parent.name == "plugins":
        return root.parent.parent, "inferred-from-plugin-path"
    host = _host_home()
    if host is not None:
        return host, "host-api"
    return Path.home() / ".hermes", "default"


def _script_tree_root() -> Path:
    """scripts/ -> add-band/ -> skills/ -> plugin root.

    The tree shipping this script is the tree the operator invoked, so it
    outranks every other candidate.
    """
    return Path(__file__).resolve().parents[3]


def _fallback_roots(home: Path) -> list[tuple[Path, str]]:
    """Where else to look when this script has no plugin tree above it — e.g. a
    pre-install copy of the skill at ``~/.hermes/skills/add-band``."""
    candidates: list[tuple[Path, str]] = [(home / "plugins" / "band", "hermes-home")]
    try:
        spec = importlib.util.find_spec("hermes_band_platform")
        if spec and spec.origin:
            candidates.append((Path(spec.origin).resolve().parent, "package"))
    except Exception:
        pass
    return candidates


def load_band_libs(root: Path) -> Optional[Any]:
    """Load the plugin's ``_band_libs`` shim **by path** — no package import.

    Importing the package would require it to be importable under some name and
    would drag in the adapter (and the host modules it imports); this needs
    neither.
    """
    path = root / "_band_libs.py"
    if not path.is_file():
        return None
    try:
        spec = importlib.util.spec_from_file_location("_band_libs_setup", path)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except Exception:
        return None


def _sdk_origin() -> Optional[str]:
    try:
        spec = importlib.util.find_spec("band")
    except Exception:
        return None
    return spec.origin if spec else None


def resolve_layout(*, require_sdk: bool = True) -> Layout:
    """Resolve the plugin tree, the Hermes home, and (optionally) the SDK.

    Sets ``HERMES_HOME`` in the environment when it can be established from a
    stronger source than the shim's own default, so ``_band_libs``'s SDK path
    *and* the install command it prints both point at the directory the gateway
    actually reads.
    """
    script_root = _script_tree_root()
    root: Optional[Path] = script_root if is_plugin_root(script_root) else None
    source: Optional[str] = "script-tree" if root is not None else None
    tried: list[dict[str, Any]] = [
        {"path": str(script_root), "source": "script-tree", "usable": root is not None}
    ]

    home, home_source = _hermes_home(root)
    if root is None:
        # Only probe elsewhere when this script has no tree of its own, so the
        # candidate list stays a record of what was actually tried.
        for candidate, how in _fallback_roots(home):
            usable = is_plugin_root(candidate)
            tried.append({"path": str(candidate), "source": how, "usable": usable})
            if usable and root is None:
                root, source = candidate, how
    # Make the shim agree with us before it computes band-libs or the hint.
    os.environ["HERMES_HOME"] = str(home)

    if root is None:
        return Layout(
            root=None,
            source=None,
            hermes_home=home,
            hermes_home_source=home_source,
            band_libs_dir=None,
            sdk_origin=_sdk_origin(),
            candidates=tried,
            error=(
                "Could not find the Band plugin tree from this script. Re-run the copy "
                "installed with the plugin (e.g. "
                f"{home / 'plugins' / 'band' / 'skills' / 'add-band' / 'scripts'}), or "
                "re-run ./install.sh to stage it."
            ),
        )

    shim = load_band_libs(root)
    band_libs = None
    error: Optional[str] = None
    if shim is not None:
        try:
            band_libs = Path(str(shim.band_libs_dir()))
        except Exception:
            band_libs = None
        if require_sdk:
            try:
                shim.ensure_band_importable()
            except Exception as exc:
                error = str(exc)
    elif require_sdk:
        error = (
            f"The plugin tree at {root} has no _band_libs.py, so the Band SDK cannot be "
            "located. Re-run ./install.sh to stage a complete tree."
        )

    origin = _sdk_origin()
    # Defense in depth: if anything (a caller's PYTHONPATH, a stray sys.path
    # entry) made `band` resolve *inside* the plugin tree, the SDK is shadowed by
    # the plugin package of the same name and `band.client` will not exist.
    if error is None and origin and Path(origin).resolve().is_relative_to(root):
        error = (
            f"`import band` resolves to the plugin package at {origin}, shadowing the "
            "Band SDK — so band.client does not exist. Something put "
            f"{root.parent} on sys.path (PYTHONPATH?); remove it and re-run."
        )

    return Layout(
        root=root,
        source=source,
        hermes_home=home,
        hermes_home_source=home_source,
        band_libs_dir=band_libs,
        sdk_origin=origin,
        candidates=tried,
        error=error,
    )
