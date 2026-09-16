"""k617-ctrl — Linux tooling for the Redragon K617 Fizz.

User commands live in the :func:`main` entry point (rgb/effect/key/paint/
animate/restore/setup-udev); the full reverse-engineering toolkit is exposed
by :func:`main_dev` via the ``k617-ctrl-dev`` console script.
"""
from .cli import main, main_dev

__all__ = ["main", "main_dev"]
__version__ = "0.1.0"