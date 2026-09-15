"""Device identity, report IDs and known-good frame knowledge for the K617 Fizz.

Sources of truth (all reverse-engineered from USB captures of the OEM software):
  * orignalbox/k617-fizz   — 5-step RGB write; INIT / P1 / P2 / EXEC
  * MrSchrodingers/fizz-rgb — firmware-effect templates + Sinodragon 382-byte per-key
  * This repo's docs/RE_GUIDE.md — the keymap/Restore sequence (work in progress)

Nothing in this module talks to hardware; it's pure constants + helpers.
"""
from __future__ import annotations

import json
from pathlib import Path

VID = 0x258A
PID = 0x0049

DATA_DIR = Path(__file__).resolve().parent / "data"

# ---- report IDs seen in HID descriptors (interface 1, vendor usage 0xFF00) ----
REPORT_IDS = [0x05, 0x06, 0x08]

# ---- known frame sizes ----
SIZE_INIT = 6
SIZE_BLOCK = 1032
SIZE_PERKEY = 382

# ---- known-good packet interiors (from the fizz-rgb firmware templates) ----
# These are complete captured frames for the "static" firmware effect.
# Index in the template list:
#   0 = INIT            (05 83 b6 00 00 00)
#   1 = mode/config     (06 08 b8 00 40 ...)
#   2 = RGB canvas base (06 09 bc 00 40 ...)
#   3 = routing         (06 09 c0 00 40 ...)  — same family as P2, never hand-edit
#   4 = EXEC/commit     (06 03 b6 00 00 ...)  — contains 5A A5 flash-commit magic
FRAME_INIT, FRAME_MODE, FRAME_CANVAS, FRAME_ROUTING, FRAME_EXEC = range(5)

TEMPLATE: list[bytes] | None = None


def base_frames(effect: str = "fw-static") -> list[bytes]:
    """Load a captured firmware-effect template as a list of raw frame bytes."""
    path = DATA_DIR / f"{effect}.json"
    with open(path) as fh:
        frames = json.load(fh)
    return [bytes(f) for f in frames]


def frame_kind(frame: bytes) -> str:
    """Identify a known frame / report by its lead bytes."""
    if not frame:
        return "empty"
    rid = frame[0]
    if rid == 0x05:
        return "INIT(05)"
    if rid == 0x06:
        head = frame[1:5]
        if head == bytes.fromhex("08b80040"):
            return "MODE(06 08 b8)"
        if head == bytes.fromhex("09bc0040"):
            return "CANVAS(06 09 bc)"
        if head == bytes.fromhex("09c00040"):
            return "ROUTING(06 09 c0)"
        if head == bytes.fromhex("03b60000"):
            return "EXEC(06 03 b6)"
        return f"BLOCK(06 {frame[1]:02x} {frame[2]:02x})"
    if rid == 0x08:
        return "PERKEY(08 0a 7a 01)" if frame[1:4] == bytes.fromhex("0a7a01") else f"REP08?({frame[1]:02x})"
    return f"report {rid:#04x}"


# ---- known split-plane RGB layout inside the 1032-byte CANVAS block ----
BLUE_BASE = 8
GREEN_BASE = 134
RED_BASE = 260

# LED index map (LED index -> key), rows with 14 data + 7 gap, stride 21.
LED_INDEX = {
    21: "Esc", 22: "1", 23: "2", 24: "3", 25: "4", 26: "5", 27: "6",
    28: "7", 29: "8", 30: "9", 31: "0", 32: "-", 33: "=", 34: "Bksp",
    42: "Tab", 43: "Q", 44: "W", 45: "E", 46: "R", 47: "T", 48: "Y",
    49: "U", 50: "I", 51: "O", 52: "P", 53: "[", 54: "]", 55: "\\",
    63: "CapsLk", 64: "A", 65: "S", 66: "D", 67: "F", 68: "G", 69: "H",
    70: "J", 71: "K", 72: "L", 73: ";", 74: "'", 76: "Enter",
    84: "LShift", 86: "Z", 87: "X", 88: "C", 89: "V", 90: "B", 91: "N",
    92: "M", 93: ",", 94: ".", 95: "/", 97: "RShift",
    105: "LCtrl", 106: "LWin", 107: "LAlt", 110: "Space", 113: "RAlt",
    114: "Fn", 117: "Menu", 118: "RCtrl",
}

NAME_TO_INDEX = {name: idx for idx, name in LED_INDEX.items()}


def set_key_color(frame: bytearray, led_index: int, rgb: tuple[int, int, int]) -> None:
    """Patch one key's RGB into a 1032-byte CANVAS frame (returns modified)."""
    r, g, b = rgb
    frame[RED_BASE + led_index] = r
    frame[GREEN_BASE + led_index] = g
    frame[BLUE_BASE + led_index] = b