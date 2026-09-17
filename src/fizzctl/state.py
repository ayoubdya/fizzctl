"""Local cache of fizzctl-created macros and their key bindings.

The K617 only exposes the **base** keymap (``05 84 d4``) and a **factory**
macro table (``05 85 dc``): macro bindings (the ``10 00 <mode> <slot>``
records) and the user's macro slots live in an overlay the device does not
return.  Reading the base keymap therefore never contains them, so we remember
what we created here and re-apply it on every keymap/macro write.

Keymaps are *not* cached — the base keymap is read straight off the device, so
binding a macro keeps your existing Cfg.ini remaps.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .macro import MAX_SLOTS


def state_path() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(Path.home(), ".local", "state")
    return Path(base) / "fizzctl" / "state.json"


def load() -> dict:
    """Load the macro cache; a missing or corrupt file yields an empty cache."""
    try:
        data = json.loads(state_path().read_text())
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("slots", {})
    data.setdefault("bindings", {})
    return data


def save(state: dict) -> None:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


def alloc_slot(state: dict, key: str) -> int:
    """Slot for ``key``: its existing one, else the lowest free slot index."""
    existing = state["bindings"].get(key)
    if existing is not None:
        return int(existing["slot"])
    used = {int(slot) for slot in state["slots"]}
    for i in range(MAX_SLOTS):
        if i not in used:
            return i
    raise ValueError(f"all {MAX_SLOTS} macro slots are in use")


def raw_slots(state: dict) -> dict[int, bytes]:
    """The cached slots as ``{index: 128-byte slot}``."""
    return {int(slot): bytes.fromhex(hex_) for slot, hex_ in state["slots"].items()}
