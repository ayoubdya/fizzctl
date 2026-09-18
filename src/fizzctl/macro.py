"""K617 macro block (06 05 dc) encoder + keymap binding.

Decoded from OEM captures (captures/*macro*.json, 7 hardware samples):

* The macro frame ``06 05 dc 00 40 …`` is 1032 bytes holding **8 slots of
  128 bytes**, slot *s* starting at ``9 + 128*s`` (slot0 @9, slot1 @137, …).
  Each slot is a 1-byte **cycle count** followed by 2-byte **events**::

      byte0 = delay_ms (0..127) | 0x80 when this event RELEASES the key
      byte1 = USB HID keyboard usage code

  e.g. the OEM's ``newmacro`` (types "rgb") decodes to
  ``109 press-R  16 press-G  63 release-R  93 release-G  63 press-B  3 release-B``.

* The macro→key **binding lives in the 1032-byte keymap block** (``06 04 d4``),
  not in the macro frame.  A bound key's 4-byte record becomes
  ``10 00 <mode> <slot>`` where ``mode`` is ``0x01`` (play ``cycle_count``
  times) or ``0x04`` (cycle until the key is released), and ``slot`` is the
  0-based macro slot.  FN-capable keys keep their output in **region B**
  (``0x218 + 4*fn_index``); other keys in their column record
  (``8 + 4*matrix_col``).

Verified offsets: key "0"→576, CapsLock→616, LAlt→660 (fn_index 10/20/31).
"""
from __future__ import annotations

MACRO_HEADER = bytes.fromhex("0605dc0040")
SLOT_BASE = 9
SLOT_STRIDE = 128
MAX_SLOTS = 8

MODE_CYCLES = 0x01          # play the macro `cycle_count` times
MODE_UNTIL_RELEASED = 0x04  # cycle until the bound key is released

ACTION_MACRO = 0x10         # keymap record byte0 for a macro binding
REGION_A_BASE = 0x08        # column records (8 + 4*col)
REGION_B_BASE = 0x218       # FN keys' base-layer output (0x218 + 4*fn_index)
REGION_B_END = 0x2d8
FRAME_LEN = 1032

MAX_DELAY_MS = 0x7F


def encode_event(delay_ms: int, hid: int, release: bool = False) -> bytes:
    """One 2-byte macro event: press (or release) ``hid`` after ``delay_ms``."""
    delay = max(0, min(MAX_DELAY_MS, int(delay_ms)))
    return bytes((delay | (0x80 if release else 0), hid & 0xFF))


def encode_slot(cycles: int, events: list[bytes]) -> bytes:
    """One 128-byte macro slot: a cycle count followed by 2-byte events."""
    slot = bytearray(SLOT_STRIDE)
    slot[0] = max(1, min(0xFF, int(cycles)))
    off = 1
    for ev in events:
        if len(ev) != 2:
            raise ValueError("each macro event must be 2 bytes")
        if off + 2 > SLOT_STRIDE:
            raise ValueError(f"macro slot overflows ({len(events)} events)")
        slot[off:off + 2] = ev
        off += 2
    return bytes(slot)


def build_macro_frame(slots: dict[int, bytes]) -> bytes:
    """Build the 1032-byte ``06 05 dc`` frame from raw 128-byte slots.

    ``slots`` maps a 0-based slot index to its 128-byte contents (from
    :func:`encode_slot`); unlisted slots stay zero (empty).
    """
    frame = bytearray(FRAME_LEN)
    frame[0:5] = MACRO_HEADER
    for idx, slot in slots.items():
        if not 0 <= idx < MAX_SLOTS:
            raise ValueError(f"slot index {idx} out of range 0..{MAX_SLOTS - 1}")
        if len(slot) != SLOT_STRIDE:
            raise ValueError(f"slot {idx} must be {SLOT_STRIDE} bytes")
        base = SLOT_BASE + idx * SLOT_STRIDE
        frame[base:base + SLOT_STRIDE] = slot
    return bytes(frame)


def decode_slot(slot: bytes) -> tuple[int, list[tuple[int, int, bool]]]:
    """Decode a 128-byte slot into ``(cycles, events)``.

    ``events`` are ``(delay_ms, hid, release)`` triples in on-wire order.
    """
    cycles = slot[0]
    events = []
    off = 1
    while off + 1 < len(slot) and slot[off] != 0:
        b0 = slot[off]
        events.append((b0 & MAX_DELAY_MS, slot[off + 1], bool(b0 & 0x80)))
        off += 2
    return cycles, events


def decode_macro_frame(frame: bytes) -> dict[int, tuple[int, list[tuple[int, int, bool]]]]:
    """Decode every non-empty slot of a 1032-byte macro frame."""
    slots = {}
    for i in range(MAX_SLOTS):
        base = SLOT_BASE + i * SLOT_STRIDE
        slot = frame[base:base + SLOT_STRIDE]
        if any(slot):
            slots[i] = decode_slot(slot)
    return slots


def encode_macro_frame(slots: list[tuple[int, list[bytes]]]) -> bytes:
    """Build the 1032-byte ``06 05 dc`` frame.

    ``slots`` is a list of ``(cycle_count, events)`` pairs, one per macro slot
    in order (slot0 first).  ``events`` is a list of 2-byte events (from
    :func:`encode_event`).
    """
    if len(slots) > MAX_SLOTS:
        raise ValueError(f"{len(slots)} macros exceeds {MAX_SLOTS} slots")
    return build_macro_frame({i: encode_slot(c, e) for i, (c, e) in enumerate(slots)})


def text_events(text: str, delay_ms: int = 30) -> list[bytes]:
    """Turn a string into press+release events (lowercase-typing).

    Characters are typed in order; each key is pressed then released with
    ``delay_ms`` between events.  Unsupported characters raise ``KeyError``.
    """
    events: list[bytes] = []
    for ch in text:
        hid = CHAR_TO_HID[ch]
        events.append(encode_event(delay_ms, hid, release=False))
        events.append(encode_event(delay_ms, hid, release=True))
    return events


def locate_key(keymap, hid: int) -> int | None:
    """Offset of the key whose base output is ``hid`` (or ``None``).

    Mirrors :func:`bind_macro`'s search order so callers can capture the
    original record before a binding overwrites it.  Region B (FN keys) wins
    over the column records.
    """
    for start, count in (
        (REGION_B_BASE, (REGION_B_END - REGION_B_BASE) // 4),
        (REGION_A_BASE, (REGION_B_BASE - REGION_A_BASE) // 4),
    ):
        for pos in range(count):
            off = start + pos * 4
            if keymap[off:off + 4] == b"\x00\x00\x00\x00":
                continue                       # unassigned slot, never a match
            if keymap[off] in (0x00, 0x06) and keymap[off + 3] == (hid & 0xFF):
                return off
    return None


def bind_macro(keymap: bytearray, hid: int, slot: int,
               mode: int = MODE_CYCLES) -> int | None:
    """Point the key whose base output is ``hid`` at macro ``slot``.

    Patches ``keymap`` in place and returns the record offset, or ``None`` if
    no matching base-layer key was found.
    """
    off = locate_key(keymap, hid)
    if off is None:
        return None
    keymap[off:off + 4] = bytes((ACTION_MACRO, 0x00, mode & 0xFF, slot & 0xFF))
    return off


def collect_bindings(keymap) -> list[tuple[int, int, int]]:
    """Every macro binding in ``keymap`` as ``(offset, mode, slot)``.

    The device echoes live macro bindings back as ``10 00 <mode> <slot>``
    records, so a read-back keymap contains them.  Region B (FN keys) is
    scanned before the column records, matching :func:`locate_key`'s order.
    """
    out: list[tuple[int, int, int]] = []
    for start, count in (
        (REGION_B_BASE, (REGION_B_END - REGION_B_BASE) // 4),
        (REGION_A_BASE, (REGION_B_BASE - REGION_A_BASE) // 4),
    ):
        for pos in range(count):
            off = start + pos * 4
            if keymap[off] == ACTION_MACRO:
                out.append((off, keymap[off + 2], keymap[off + 3]))
    return out


def slots_in_frame(frame: bytes) -> dict[int, bytes]:
    """The non-empty slots of a macro frame as ``{index: 128-byte slot}``."""
    slots: dict[int, bytes] = {}
    for i in range(MAX_SLOTS):
        base = SLOT_BASE + i * SLOT_STRIDE
        slot = frame[base:base + SLOT_STRIDE]
        if any(slot):
            slots[i] = slot
    return slots


def relocate_bindings(live: bytes, target: bytearray,
                      oracle: bytes | None = None) -> tuple[bytes, list[str]]:
    """Re-apply the macro bindings read from ``live`` onto ``target``.

    Bindings are matched to their **physical key** by the live layout, then
    written at that key's position in ``target``.  Layouts are free to move
    keys (e.g. an ``Alt`` that is an FN-layered key at offset 660 in one
    layout and a plain column-17 key at offset 76 in another); copying raw
    offsets would drop the binding onto a different key.

    A binding below the FN area (``< 0x218``) is a plain key, its column is
    the offset's own column.  A binding in the FN area is the key whose column
    record points at that FN slot (``02 00 00 <slot>``).  If the live layout
    can't identify the key (an orphan binding left behind by an earlier
    layout—nothing points at its FN slot), ``oracle`` (a captured keymap, e.g.
    ``CONST_KEYMAP``) is consulted at the same offset and the key located in
    ``target`` by its HID.
    """
    target = bytearray(target)
    warnings: list[str] = []
    n = 0
    for off, mode, slot in collect_bindings(live):
        if off < REGION_B_BASE:
            col = (off - REGION_A_BASE) // 4
        else:
            fp = (off - REGION_B_BASE) // 4
            cols = [
                c for c in range((REGION_B_BASE - REGION_A_BASE) // 4)
                if live[REGION_A_BASE + c * 4] == 0x02
                and live[REGION_A_BASE + c * 4 + 3] == fp
            ]
            col = cols[0] if len(cols) == 1 else None
        if col is not None:
            rec_off = REGION_A_BASE + col * 4
            kind = target[rec_off]
            if kind == 0x02:                     # FN-layered key here too -> follow it
                dst = REGION_B_BASE + target[rec_off + 3] * 4
            elif kind in (0x00, 0x06):           # plain key -> its own column record
                dst = rec_off
            else:
                warnings.append(
                    f"macro binding @+{off:#06x}: column {col} is not bindable in this "
                    f"keymap (record {target[rec_off:rec_off + 4].hex(' ')})")
                continue
        elif oracle is not None:
            rec = oracle[off:off + 4]
            if rec[0] in (0x00, 0x06):
                dst = locate_key(target, rec[3])
                warnings.append(
                    f"macro binding @+{off:#06x}: located as key "
                    f"0x{rec[3]:02x} (moved from @+{off:#06x} to @+{dst:#06x})" if dst is not None
                    else f"macro binding @+{off:#06x}: cannot identify its key")
                if dst is None:
                    continue
            else:
                warnings.append(f"macro binding @+{off:#06x}: cannot identify its key")
                continue
        else:
            warnings.append(f"macro binding @+{off:#06x}: cannot identify its key")
            continue
        target[dst:dst + 4] = bytes((ACTION_MACRO, 0x00, mode & 0xFF, slot & 0xFF))
        n += 1
    return bytes(target), warnings


# --------------------------------------------------------------------------
# key / character tables
# --------------------------------------------------------------------------

# CLI key name -> USB HID usage code (the keys present on the 60% K617).
NAME_TO_HID: dict[str, int] = {
    "Esc": 0x29,
    "1": 0x1E, "2": 0x1F, "3": 0x20, "4": 0x21, "5": 0x22, "6": 0x23,
    "7": 0x24, "8": 0x25, "9": 0x26, "0": 0x27, "-": 0x2D, "=": 0x2E,
    "Bksp": 0x2A,
    "Tab": 0x2B,
    "Q": 0x14, "W": 0x1A, "E": 0x08, "R": 0x15, "T": 0x17, "Y": 0x1C,
    "U": 0x18, "I": 0x0C, "O": 0x12, "P": 0x13, "[": 0x2F, "]": 0x30,
    "\\": 0x31,
    "CapsLk": 0x39,
    "A": 0x04, "S": 0x16, "D": 0x07, "F": 0x09, "G": 0x0A, "H": 0x0B,
    "J": 0x0D, "K": 0x0E, "L": 0x0F, ";": 0x33, "'": 0x34, "Enter": 0x28,
    "LShift": 0xE1,
    "Z": 0x1D, "X": 0x1B, "C": 0x06, "V": 0x19, "B": 0x05, "N": 0x11,
    "M": 0x10, ",": 0x36, ".": 0x37, "/": 0x38, "RShift": 0xE5,
    "LCtrl": 0xE0, "LWin": 0xE3, "LAlt": 0xE2, "Space": 0x2C,
    "RAlt": 0xE6, "Menu": 0x65, "RCtrl": 0xE4,
}
# common long spellings
NAME_TO_HID["CapsLock"] = NAME_TO_HID["CapsLk"]
NAME_TO_HID["Backspace"] = NAME_TO_HID["Bksp"]

# characters that `text_events` can type (letters type lowercase, no modifiers)
CHAR_TO_HID: dict[str, int] = {}
for _i, _c in enumerate("abcdefghijklmnopqrstuvwxyz"):
    CHAR_TO_HID[_c] = 0x04 + _i
    CHAR_TO_HID[_c.upper()] = 0x04 + _i
for _i, _c in enumerate("123456789"):
    CHAR_TO_HID[_c] = 0x1E + _i
CHAR_TO_HID["0"] = 0x27
CHAR_TO_HID.update({
    " ": 0x2C, "\n": 0x28, "\t": 0x2B, "-": 0x2D, "=": 0x2E, "[": 0x2F,
    "]": 0x30, "\\": 0x31, ";": 0x33, "'": 0x34, "`": 0x35, ",": 0x36,
    ".": 0x37, "/": 0x38,
})
