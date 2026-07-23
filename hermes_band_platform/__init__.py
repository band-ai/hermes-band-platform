from ._band_libs import bootstrap

# Must run before ``.adapter`` is imported: its module-top SDK import guard
# binds at import time, and directory installs resolve the SDK from
# ``$HERMES_HOME/band-libs``. Never raises — a missing SDK is logged with the
# exact fix and the adapter degrades (``BAND_AVAILABLE = False``) so Band
# stays registered and visible as a channel needing install, rather than
# disappearing from the gateway entirely.
bootstrap()

from .adapter import register  # noqa: E402

__version__ = "1.0.0"

__all__ = ["register", "__version__"]
