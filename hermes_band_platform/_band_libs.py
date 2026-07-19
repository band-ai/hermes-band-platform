"""Resolve the Band SDK from ``$HERMES_HOME/band-libs`` (directory installs).

Directory plugins ship files only — Hermes does not install their Python
dependencies (see ``docs/hermes-plugin-dependency-metadata-issue.md``), and on
hosted runtimes the gateway's own site-packages is read-only, so ``band-sdk``
cannot live there. The installer instead resolves it into a user-writable
target dir::

    uv pip install --python "<gateway_python>" \
        --target "$HERMES_HOME/band-libs" "band-sdk>=1.0.0,<2.0.0"

and this shim prepends that dir to ``sys.path`` before anything imports
``band``. Prepending (not appending) makes ``band-libs`` authoritative when
both it and a site-packages copy exist, so upgrading the target dir always
takes effect.

``ensure_band_importable()`` is called from the package ``__init__`` — i.e.
before ``adapter.py``'s module-top SDK import guard binds — and raises ONE
clear, actionable error when the SDK is missing everywhere, instead of letting
the plugin load into a silently broken state.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

BAND_SDK_SPEC = "band-sdk>=1.0.0,<2.0.0"


def hermes_home() -> Path:
    """The Hermes home dir — ``$HERMES_HOME`` if set, else ``~/.hermes``."""
    override = os.environ.get("HERMES_HOME", "").strip()
    return Path(override).expanduser() if override else Path.home() / ".hermes"


def band_libs_dir() -> Path:
    """Where the installer targets the Band SDK: ``$HERMES_HOME/band-libs``."""
    return hermes_home() / "band-libs"


def prepend_band_libs() -> Path | None:
    """Prepend ``band-libs`` to ``sys.path`` if it exists. Returns the path used."""
    libs = band_libs_dir()
    if not libs.is_dir():
        return None
    entry = str(libs)
    if entry in sys.path:
        return libs
    sys.path.insert(0, entry)
    return libs


def ensure_band_importable() -> None:
    """Make ``import band`` resolvable, or raise one clear, actionable error.

    Path order: an already-imported/stubbed ``band`` (tests) wins; otherwise
    ``band-libs`` is prepended so a directory install resolves its own SDK;
    site-packages remains the natural fallback for wheel installs.
    """
    if "band" in sys.modules:  # real SDK or a test stub — nothing to do
        return
    libs = prepend_band_libs()
    try:
        found = importlib.util.find_spec("band") is not None
    except (ImportError, ValueError):
        found = False
    if found:
        return
    checked = str(libs) if libs is not None else f"{band_libs_dir()} (not present)"
    raise ImportError(
        "The Band SDK is not importable by the Hermes gateway "
        f"(checked {checked} and the interpreter's own packages). "
        "Fix it with:\n"
        f'  uv pip install --python "{sys.executable}" '
        f'--target "{band_libs_dir()}" "{BAND_SDK_SPEC}"\n'
        "then restart the gateway. No sudo and no write to the gateway's "
        "site-packages is required."
    )
