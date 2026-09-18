# Lighting, effects, per-key painting

Two independent subsystems, both in `src/fizzctl/effects.py`, `blobs.py`,
`protocol.py`:

1. **Firmware-native effects** — a 5-frame fw-static burst (INIT, MODE, CANVAS,
   ROUTING, EXEC) patched in a few bytes.
2. **Per-key painting** — a single 382-byte `08 0a 7a 01` report, volatile,
   no handshake, no flash commit.

## Firmware effects

`encode_firmware_effect` starts from the captured fw-static template
(`base_frames()`) and patches only:

| Location | Meaning |
|---|---|
| `EXEC[21]` | effect id = 1-indexed position in the OEM software menu (Fixed_on=0x01, Rainbow=0x03, … Blossom=0x11; the un-captured ids are inferred by position and marked `pending-live-verify`) |
| `EXEC[38 + 2*(id-1)]` | per-effect RGB/color **toggle**: `0x07` multicolor, `0x00` render the MODE base color |
| `EXEC[39]` | live speed×brightness, packed: high nibble = speed|speed is stored **0-based** (the firmware displays `nibble+1` as 1..5), low nibble = brightness 0..4 raw |
| `EXEC[39 + 2*(id-1)]` | per-effect remembered speed/brightness (ids 1..20) — must land here **and** in `EXEC[39]` or per-effect speed is ignored |
| `MODE[color_slot]` | base color, **effect-dependent**: |

Base color slots (verified, not guessed):

```
fixed-on  -> MODE[29,30,31]   (live green on hardware)
sine-wave -> MODE[281,282,283](red/green from OEM captures; [280]=0xff const)
everything else -> MODE[218,219,220]  (snake-verified blue on hardware)
```

The color mechanism cost a few iterations:

- The stock templates bake *every* effect's toggle byte to `0x07`
  (multicolor/"RGB" checkbox ON), which is why colors silently did nothing.
- Setting the flag requires the effect's **own** slot — `EXEC[56]` is only
  snake's slot (id 10: `38 + 2*(10-1) = 56`), not a global toggle.
- Speed was stored raw at first (0-based vs displayed +1); `--speed 1` showed
  2 on a sine-wave before the fix.

22 effects total, `off` = id 0x16 with default `0x00`.

## CANVAS (`06 09 bc`) and per-key painting

The 1032-byte CANVAS block packs per-key RGB in split planes:

```
BLUE_BASE  = 8    GREEN_BASE = 134   RED_BASE = 260
```

Each row on the keyboard has 14 data bytes then a 7-byte gap, stride 21; LED
indices 0..20 per row (see `protocol.LED_INDEX`, keys `Esc`..`RCtrl`).  Paint
writes the block + `EXEC` commit (`rgb_sequence`).

The **volatile** per-key report is separate:

```
08 0a 7a 01  |  96 RGB triplets, column-major raster  pos = col*6 + row
```

- Esc = **1** (not 0 — an earlier guess had Esc=0, and Menu=65/RCtrl=71, which
  landed on dead positions; corrected and hardware-verified to Esc=1, Menu=77,
  RCtrl=83).
- No handshake, no flash write — frames are re-streamed continuously for
  host-side animations (`animations.py`).

## Reading live lighting

`read_lighting` reads MODE/CANVAS/ROUTING/EXEC via their selectors
(`protocol.md`) so a keymap/macro write can echo the *current* effect, color
and brightness instead of resetting them to the baked stock frames.
`_live_lighting` falls back to the stock frames if a read fails.