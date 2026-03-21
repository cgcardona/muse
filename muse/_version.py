"""Single source of truth for the Muse package version.

All schema_version fields across the codebase read from here rather than
hardcoding a number.  The version itself lives in ``pyproject.toml`` and is
injected into the installed package metadata at build time.
"""

from importlib.metadata import version

__version__: str = version("muse")
