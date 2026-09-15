"""k617-ctrl — Linux tooling for the Redragon K617 Fizz.

Command-line entry point; see k617_ctrl.cli for the subcommands.
"""
from .cli import main

__all__ = ["main"]
__version__ = "0.1.0"