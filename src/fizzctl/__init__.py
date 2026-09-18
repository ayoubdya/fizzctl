"""fizzctl — Linux tooling for the Redragon K617 Fizz.

User commands live in the :func:`main` entry point (rgb/effect/key/paint/
animate/keymap/setup-udev); the full reverse-engineering toolkit is exposed
by :func:`main_dev` via the ``fizzctl-dev`` console script.
"""
from .cli import main, main_dev

__all__ = ["main", "main_dev"]
__version__ = "0.4.0"