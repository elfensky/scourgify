"""scourgify — normalize a FanFicFare-imported Calibre library."""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _dist_version

try:
    __version__ = _dist_version("scourgify")
except PackageNotFoundError:  # source checkout that was never installed
    __version__ = "0.0.0+local"
