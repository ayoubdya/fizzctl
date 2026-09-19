"""fizzctl — Linux tooling for the Redragon K617 Fizz.

User commands live in the :func:`main` entry point (rgb/effect/key/paint/
animate/keymap/setup-udev); the full reverse-engineering toolkit is exposed
by :func:`main_dev` via the ``fizzctl-dev`` console script.
"""
from importlib.metadata import PackageNotFoundError, version as _installed

from .cli import main, main_dev

__all__ = ["main", "main_dev", "version"]


def version() -> str:
    """The package version, derived from pyproject.toml at install time."""
    try:
        return _installed("fizzctl")
    except PackageNotFoundError:
        return "0.0.0"