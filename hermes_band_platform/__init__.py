from ._band_libs import ensure_band_importable

# Must run before ``.adapter`` is imported: its module-top SDK import guard
# binds at import time, and directory installs resolve the SDK from
# ``$HERMES_HOME/band-libs`` (raises one actionable error when missing).
ensure_band_importable()

from .adapter import register  # noqa: E402

__version__ = "1.0.0"

__all__ = ["register", "__version__"]
