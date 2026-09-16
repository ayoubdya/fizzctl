# k617-ctrl — Linux tooling for the Redragon K617 Fizz

Reverse-engineering workspace + reference controllers for the keyboard's
vendor HID interface (`258a:0049`, interface 1, usage page `0xFF00`).

## Status

| Capability | Status |
|---|---|
| Per-key RGB write (5-frame flash-commit protocol) | ✅ working reference |
| 22 firmware-native effects (matching official software) w/ speed+brightness | ✅ working |
| Host-side per-key animations (solid/blink/pulse/chase/wave/rainbow/drop) | ✅ working |
| Per-key painting (single key or many) via CANVAS + 5AA5 flash commit | ✅ working |
| Cfg.ini parser (`[OPT]`/`[FN]`/`[KEY]`) | ✅ working |
| USB capture inspect / one-key-change diff | ✅ working |
| Capture-to-frames export + dry-run replay | ✅ working |
| **Keymap Restore write on Linux** | ✅ works — verified byte-for-byte against captures |

## Layout

```
src/k617_ctrl/
  __init__.py    package entry point → cli.main()
  cli.py         CLI entry point (list/cfg/inspect/diff/export/replay/rgb/effect/key/paint/animate/restore)
  protocol.py    constants, report IDs, known frame kinds, RGB plane layout
  effects.py     firmware-effect table + encoder, per-key frame encoder + LED pos map
  animations.py  host-side per-key animations (streamed, volatile)
  cfg.py         Cfg.ini parser → mapping triples + matrix coords
  keymap.py      06 04 d4 keymap-block encoder (reproduces captures exactly)
  hid.py         hidapi wrapper + known-good RGB sequence builders (send_rgb/send_firmware_effect/send_per_key)
  capture.py     tshark-JSON / usbmon-pcap import, diff, export
  blobs.py       captured firmware frames inlined as hex (no binary files)
captures/        tshark JSON exports (RE datasets; not shipped in the wheel)
frames/          exported replay frames (JSON)
docs/RE_GUIDE.md the keymap reverse-engineering playbook
analyze.py       one-shot RE inspection over all captures
```

## Quickstart (uv)

```bash
uv sync
uv run k617-ctrl list
uv run k617-ctrl cfg ../redragon-k617-key-remap/Cfg.ini
# USB capture inspect / one-key-change diff
uv run k617-ctrl inspect captures/r3.json
uv run k617-ctrl diff   captures/r2.json captures/r3.json
uv run k617-ctrl export captures/r2.json frames.json
uv run k617-ctrl replay frames.json --dry-run
```

## RGB lighting quickstart

```bash
# 22 firmware-native effects (flash-commit, survive power-off).
# Colors/speed/brightness optional.
# Supports full official Redragon list: fixed-on (static), respire, rainbow,
# flash-away, raindrops, rainbow-wheel (wheel), ripples-shining, stars-twinkle,
# shadow-disappear, retro-snake (snake), neon-stream, reaction, sine-wave,
# retinue-scanning, rotating-windmill, colorful-waterfall (waterfall),
# blossoming (rainbow-blossom), rotating-storm, collision, perfect,
# self-define, off.
uv run k617-ctrl effect rainbow --speed 2 --brightness 4
uv run k617-ctrl effect fixed-on --color 00ff00 --brightness 3
# or using aliases:
uv run k617-ctrl effect static   --color 00ff00 --brightness 3

# Per-key painting (flash write, persists across power-off).
# Keys are named by the top-left legend (Esc 1 2 .. Fn) or single-char labels.
# This is the k617-fizz `send_colors` CANVAS path: a full canvas is written,
# so any key NOT listed turns off.
uv run k617-ctrl key W ff0000
uv run k617-ctrl paint W=ff0000 A=00ff00 Space=0000ff

# Host-side animations (volatile stream; Ctrl+C to stop, or --duration to auto-stop).
# Names: solid blink pulse chase wave rainbow drop
uv run k617-ctrl animate rainbow --fps 30 --duration 10
uv run k617-ctrl animate chase --color ff0000 --speed 2

# Colors: name (red green blue white black) or #RRGGBB hex.

# set the whole board to one color (firmware static effect)
printf 'y\n' | uv run k617-ctrl effect static --color ffffff --brightness 4
```

### How the RGB paths differ

* **Flash writes** (the `rgb`, `key`, `paint`, `effect` and `restore`
  commands) send the 4-5 frame burst (INIT → GET_REPORT handshake →
  [MODE] → CANVAS → ROUTING → EXEC) ending in a `5AA5` commit. Key colors are
  patched into the CANVAS split-plane (red@260 + idx / green@134 + idx /
  blue@8 + idx, stride-21 LED map); effects patch MODE[29..31] for color and
  EXEC[21] for the mode id. Everything survives power-off. Any key *not* in a
  `key`/`paint` canvas turns off — the canvas is absolute, matching
  k617-fizz `send_colors`.
* **Animations** (`animate`) push a single 382-byte Sinodragon report
  (`08 0A 7A 01` + 96 RGB triplets, 16-col × 6-row raster, pos = col*6+row)
  per frame at 30fps, with no handshake and no flash write — strictly
  host-side and lost on the next reboot/reconnect.
* **Display reset:** after a reboot the board falls back to the last committed
  flash state. Once `animate` stops, the board shows the last flashed effect
  again.
* **LED layout:** the CANVAS LED map is the stride-21 layout from Cfg.ini
  (identical to k617-fizz). The Sinodragon per-key raster matches `fizz-rgb`
  with 3 corrections found on this unit: `Esc=1` (not 0 — the firmware ignores
  key 0), `Menu=77` and `RCtrl=83` (upstream maps 65/71, which land on dead
  positions).

Or install it as a package:

```bash
uv build                      # build sdist + wheel
uv tool install .             # or: pip install dist/*.whl
k617-ctrl --help
```

## Data provenance

The constant frame blocks (MODE / CANVAS / ROUTING / EXEC) and the RGB
static-effect template used to be shipped as separate binary/JSON files.
They are now inlined as hex in `src/k617_ctrl/blobs.py`, so the wheel is
self-contained — no `data/` directory needed at runtime.

## HID access without root

Feature reports to the vendor interface need read/write on `/dev/hidraw*`.
Add a udev rule (needs one `sudo`):

```
KERNEL=="hidraw*", ATTRS{idVendor}=="258a", ATTRS{idProduct}=="0049", MODE="0660", GROUP="input"
```
then `sudo udevadm control --reload && sudo udevadm trigger`.

## Warnings

* The `rgb`, `key`, `paint`, `effect` and `restore` commands and any `replay`
  of a Restore capture **commit to flash** (`5AA5` magic). Do not loop them.
  Full-flash backup first:
  `sinowealth-kb-tool read -d redragon-k617-fizz --section full backup.hex`
* `animate` is the only RAM-only path — it streams per-key reports with no
  `5AA5` commit (see the Status table).
* Some captures contain GET_REPORT responses (device→host). The tool only
  exports host→device SET_REPORT payloads for replay.
* The firmware has no RAM-only path — every write is a flash write.