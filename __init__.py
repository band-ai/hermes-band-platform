"""Directory-plugin shim for Hermes Git installs.

Hermes loads Git-installed directory plugins from the repository root as
``hermes_plugins.<name>``. The importable Python package lives in
``hermes_band_platform/`` for wheel installs, so this root shim delegates the
directory-plugin entry point to the packaged implementation.
"""

try:
    from .hermes_band_platform import register
except ImportError:
    from hermes_band_platform import register


__all__ = ["register"]
