"""Firmware-native effects and the Sinodragon per-key protocol.

Two independent protocols:

* Firmware effects — a 5-frame burst, all built from the fw-static template
  by patching a few bytes:
      MODE[218..220] = base color (R,G,B) — the real color field (per-effect
                       slot; sine-wave uses MODE[281..283])
      EXEC[38+2*(id-1)] = per-effect RGB/color toggle: 0x07 multicolor,
                         0x00 render the MODE base color
      EXEC[21]       = effect_id (selects rainbow/snake/wheel/...)
      EXEC[39]       = packed nibbles (high=speed-1 (0..4), low=brightness 0..4)
  Sending requires the mandatory GET_REPORT(0x06, 1032) handshake after INIT
  (without it the firmware silently ignores the burst).

  Speed is stored 0-based: the firmware displays ``nibble + 1`` as speed
  1..5, so passing ``--speed 1`` stores 0.  Verified on hardware (sine-wave:
  sending 1/2/3 showed 2/3/4 before the fix).  Brightness is stored raw
  (0..4).  The color mechanism was verified from OEM captures (same effect in
  red/green/blue differs only at the effect's MODE color slots, with that
  effect's flag byte zeroed); all stock templates bake every flag byte to
  0x07 (multicolor), so the base color needs the effect's own flag flipped to
  0x00 to take effect — EXEC[56] (the old assumption) is only snake's slot.

* Per-key paint — a SINGLE 382-byte feature report ``08 0a 7a 01`` followed by
  96 RGB triplets in a 16-col x 6-row column-major raster (pos = col*6+row).
  No handshake, no flash commit, host-side (volatile) — bytes are re-applied
  every frame for animations.  Verified on hardware: Esc=1, Menu=77, RCtrl=83
  (an earlier mapping had Esc=0/Menu=65/RCtrl=71, which lands on dead
  positions).
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Firmware effects
# ---------------------------------------------------------------------------

# Baseline is the captured fw-static template (init + mode + canvas + routing
# + exec).  Every other effect only differs in the 3-5 bytes listed below.
# Indexes into base_frames(): 0=INIT, 1=MODE, 2=CANVAS, 3=ROUTING, 4=EXEC.
from .protocol import base_frames

# Official Redragon software effect menu (order from K617 software).
# The effect id byte at EXEC[21] equals the 1-indexed position in that menu:
# 8 independently-captured effects all land exactly
# on their menu position (Fixed_on=0x01, Rainbow=0x03, ... Blossom=0x11), so
# the un-captured ids are inferred by position and marked ``pending-live-verify``.
# ``name`` is the canonical slug; ``aliases`` keep old short names working.
EFFECTS = [
    # (name,            aliases,             id,   accepts_color, default_sb,  notes)
    ("fixed-on",        ("static",),         0x01, True,  0x33, ""),
    ("respire",         (),                  0x02, True,  0x33, ""),
    ("rainbow",         (),                  0x03, True,  0x33, ""),
    ("flash-away",      (),                  0x04, True,  0x33, ""),
    ("raindrops",       (),                  0x05, True,  0x33, ""),
    ("rainbow-wheel",   ("wheel",),          0x06, True,  0x33, ""),
    ("ripples-shining", (),                  0x07, True,  0x33, ""),
    ("stars-twinkle",   ("star-twinkle",),   0x08, True,  0x33, ""),
    ("shadow-disappear", (),                 0x09, True,  0x33, ""),
    ("retro-snake",     ("snake",),          0x0a, True,  0x44, ""),
    ("neon-stream",     (),                  0x0b, True,  0x44, ""),
    ("reaction",        (),                  0x0c, True,  0x44, ""),
    ("sine-wave",       (),                  0x0d, True,  0x44, ""),
    ("retinue-scanning", (),                 0x0e, True,  0x44, ""),
    ("rotating-windmill", (),                0x0f, True,  0x33, ""),
    ("colorful-waterfall", ("waterfall",),   0x10, True,  0x33, ""),
    ("blossoming",      ("rainbow-blossom",), 0x11, True,  0x44, ""),
    ("rotating-storm",  (),                  0x12, True,  0x33, ""),
    ("collision",       (),                  0x13, True,  0x33, ""),
    ("perfect",         (),                  0x14, True,  0x33, ""),
    ("self-define",     (),                  0x15, True,  0x33, ""),
    ("off",             ("off",),            0x16, True,  0x00, ""),
]

# The base color slot in the MODE frame is effect-specific.  Verified:
#   fixed-on (0x01)   -> MODE[29..31]   live-tested green on hardware
#   snake (0x0a)      -> MODE[218,219,220]  live-tested blue on hardware
#   sine-wave (0x0d)  -> MODE[280..284]-ish: red/green OEM captures moved
#       bytes [281]=R and [282]=G (byte [280]=0xff constant; B at [283]
#       inferred since both captured colors had B=0).  Everything else
#       defaults to the [218..220] slot (snake-verified).
_COLOR_SLOTS: dict[str, tuple[int, int, int]] = {
    "fixed-on": (29, 30, 31),
    "sine-wave": (281, 282, 283),
}


def _color_slot(name: str) -> tuple[int, int, int]:
    return _COLOR_SLOTS.get(name, (218, 219, 220))


EFFECT_ID: dict[str, int] = {n: eid for n, _, eid, *_ in EFFECTS}
_ALIASES: dict[str, str] = {a: n for n, as_, *_ in EFFECTS for a in as_}
EFFECT_ACCEPTS_COLOR = {n for n, _, _, ac, *_ in EFFECTS if ac}
EFFECT_DEFAULTS = {n: (sb >> 4, sb & 0x0F) for n, _, _, _, sb, *_ in EFFECTS}


def _canonical(name: str) -> str:
    if name in EFFECT_ID:
        return name
    canon = _ALIASES.get(name)
    if canon is None:
        raise ValueError(
            f"unknown effect {name!r}; choose from {', '.join(effect_names())}"
        )
    return canon


def effect_names() -> list[str]:
    return list(EFFECT_ID)


def encode_firmware_effect(
    name: str,
    color: tuple[int, int, int] | None = None,
    speed: int | None = None,
    brightness: int | None = None,
) -> list[bytes]:
    """Encode the 5-frame burst (INIT, MODE, CANVAS, ROUTING, EXEC) for an
    effect.  Patches the effect's MODE color slot, EXEC[21] (effect_id), its
    EXEC[38+2*(id-1)] RGB/color toggle and the speed/brightness onto the
    fw-static baseline.

    The color mechanism was established from OEM captures: applying the same
    effect in red/green/blue differs ONLY at the MODE color slot (snake:
    MODE[218..220]; sine-wave: MODE[281..283]).  Each effect owns an
    RGB/color toggle byte at EXEC[38+2*(id-1)]: 0x07 = multicolor animation,
    0x00 = render the MODE base color instead.  Stock templates bake every
    toggle to 0x07 (the OEM's "RGB" checkbox is ON by default), which is why
    colors silently never took effect before — and EXEC[56] is only snake's
    toggle, not a global one.

    OEM slider ranges: speed 1..5, brightness 0..4 (5 levels each).  Values
    are clamped when the user passes them; missing flags keep the template
    default (so ``off`` at 0x00 is preserved).  A color is only applied when
    the effect accepts one; when no color is given the baked RGB-mode flag
    is left untouched.
    """
    name = _canonical(name)
    eid = EFFECT_ID[name]
    defaults = EFFECT_DEFAULTS[name]

    frames = [bytearray(f) for f in base_frames()]
    mode, canvas, routing, exec_ = frames[1], frames[2], frames[3], frames[4]

    if color is not None and name in EFFECT_ACCEPTS_COLOR:
        r, g, b = color[0] & 0xFF, color[1] & 0xFF, color[2] & 0xFF
        ro, go, bo = _color_slot(name)
        mode[ro], mode[go], mode[bo] = r, g, b
        # EXEC[38 + 2*(eid-1)] is this effect's RGB/single-color flag byte
        # (0x07 = RGB/random, 0x00 = render MODE color).  EXEC[56] is the
        # snake (eid 10) slot -- not a global toggle, which is why sine stayed
        # multicolor before the per-effect slot was used.
        exec_[38 + 2 * (eid - 1)] = 0x00

    exec_[21] = eid

    # EXEC[39] is the live speed×brightness value (high nibble = speed,
    # low nibble = brightness).  Evidence: an OEM solid-color capture carries
    # 0x32 there, the fw-static template 0x34, and the OEM keeps per-effect
    # remembered values in a 20-slot table at EXEC[39 + 2*(id-1)] (ids 1..20;
    # the same capture shows saved 0x44 in slot 5 and 0x22 in slot 12).  The
    # value must land in both places or per-effect speed/brightness is ignored.
    # Speed is stored 0-based: the OEM displays nibble+1 (hardware-verified).
    new_speed = (max(1, min(5, round(speed))) - 1) if speed is not None else defaults[0]
    new_bright = max(0, min(4, round(brightness))) if brightness is not None else defaults[1]
    packed = ((new_speed & 0x0F) << 4) | (new_bright & 0x0F)
    exec_[39] = packed
    if 1 <= eid <= 20:
        exec_[39 + 2 * (eid - 1)] = packed

    return [bytes(frames[0])] + [bytes(f) for f in frames[1:]]


# ---------------------------------------------------------------------------
# Per-key (Sinodragon) protocol — 382-byte single report
# ---------------------------------------------------------------------------

PERKEY_HEADER = bytes.fromhex("080a7a01")
PERKEY_PACKET_LEN = 382

# K617 key name -> position in the 16x6 column-major raster (pos = col*6+row).
# Rows 1..4 in the 6-row raster hold rows 0..4 of the keyboard; raster row 0
# is "phantom"/unused except Esc which lives at col0/row1 (=1), NOT 0.
# All verified on hardware except where noted.
PER_KEY_POS = {
    # Row 0 — number row
    "Esc": 1, "1": 7, "2": 13, "3": 19, "4": 25,
    "5": 31, "6": 37, "7": 43, "8": 49, "9": 55,
    "0": 61, "-": 67, "=": 73, "Bksp": 79,
    # Row 1 — QWERTY
    "Tab": 2,
    "Q": 8, "W": 14, "E": 20, "R": 26, "T": 32, "Y": 38,
    "U": 44, "I": 50, "O": 56, "P": 62, "[": 68, "]": 74, "\\": 80,
    # Row 2 — home row
    "CapsLk": 3,
    "A": 9, "S": 15, "D": 21, "F": 27, "G": 33, "H": 39,
    "J": 45, "K": 51, "L": 57, ";": 63, "'": 69, "Enter": 81,
    # Row 3 — bottom row
    "LShift": 4,
    "Z": 10, "X": 16, "C": 22, "V": 28, "B": 34, "N": 40,
    "M": 46, ",": 52, ".": 58, "/": 64, "RShift": 82,
    # Row 4 — modifier row
    "LCtrl": 5, "LWin": 11, "LAlt": 17, "Space": 35, "RAlt": 53,
    "Fn": 59, "Menu": 77, "RCtrl": 83,
}


def encode_per_key_frame(colors: dict[str, tuple[int, int, int]] | None = None) -> bytes:
    """Build the single 382-byte per-key report.  `colors` maps key name ->
    (r,g,b); keys not listed light as off.  `None` = all keys black."""
    frame = bytearray(PERKEY_PACKET_LEN)
    frame[0:4] = PERKEY_HEADER
    for key, (r, g, b) in (colors or {}).items():
        pos = PER_KEY_POS.get(key)
        if pos is None:
            raise KeyError(f"unknown key {key!r} for per-key paint")
        off = 4 + pos * 3
        frame[off], frame[off + 1], frame[off + 2] = r & 0xFF, g & 0xFF, b & 0xFF
    return bytes(frame)


def encode_per_key_solid(color: tuple[int, int, int]) -> bytes:
    return encode_per_key_frame({key: color for key in PER_KEY_POS})


def parse_color(s: str) -> tuple[int, int, int] | None:
    """Parse a color name or hex string into (r, g, b)."""
    named = {
        # primary
        "red": (255, 0, 0), "green": (0, 255, 0), "blue": (0, 0, 255),
        # secondary
        "yellow": (255, 255, 0), "cyan": (0, 255, 255), "magenta": (255, 0, 255),
        "purple": (128, 0, 255), "orange": (255, 165, 0), "pink": (255, 105, 180),
        "lime": (191, 255, 0), "teal": (0, 128, 128), "violet": (238, 130, 238),
        "brown": (165, 42, 42), "gold": (255, 215, 0), "silver": (192, 192, 192),
        "gray": (128, 128, 128), "grey": (128, 128, 128),
        "navy": (0, 0, 128), "maroon": (128, 0, 0), "olive": (128, 128, 0),
        "coral": (255, 127, 80), "indigo": (75, 0, 130), "salmon": (250, 128, 114),
        # light / shades
        "lightred": (255, 102, 102), "lightgreen": (144, 238, 144),
        "lightblue": (173, 216, 230),
        "darkred": (139, 0, 0), "darkgreen": (0, 100, 0), "darkblue": (0, 0, 139),
        # neutral
        "white": (255, 255, 255), "off": (0, 0, 0), "black": (0, 0, 0),
    }
    s = s.strip().lstrip("#")
    key = s.lower().replace("_", "").replace("-", "").replace(" ", "")
    if key in named:
        return named[key]
    if len(s) == 6:
        try:
            return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
        except ValueError:
            return None
    return None


# Re-export the RGB canvas pieces used by the "static canvas" path so callers
# only need one import site.
__all__ = [
    "EFFECTS", "EFFECT_ID", "EFFECT_ACCEPTS_COLOR", "EFFECT_DEFAULTS",
    "effect_names", "encode_firmware_effect", "PERKEY_HEADER",
    "PERKEY_PACKET_LEN", "PER_KEY_POS",
    "encode_per_key_frame", "encode_per_key_solid",
]