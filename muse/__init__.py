"""Muse — domain-agnostic version control for multidimensional state."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__: str = version("muse")
except PackageNotFoundError:
    __version__ = "0.0.0+dev"
