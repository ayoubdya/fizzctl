# k617-ctrl — Linux tooling for the Redragon K617 Fizz

Reverse-engineering workspace + reference controllers for the keyboard's
vendor HID interface (`258a:0049`, interface 1, usage page `0xFF00`).

## Status

| Capability | Status |
|---|---|
| Per-key RGB write (5-frame flash-commit protocol) | ✅ working reference |
| Cfg.ini parser (`[OPT]`/`[FN]`/`[KEY]`) | ✅ working |
| USB capture inspect / one-key-change diff | ✅ working |
| Capture-to-frames export + dry-run replay | ✅ working |
| **Keymap Restore write on Linux** | ✅ works — verified byte-for-byte against captures |

## Layout

```
src/k617_ctrl/
  __init__.py    package entry point → cli.main()
  cli.py         CLI entry point (list/cfg/inspect/diff/export/replay/rgb/restore)
  protocol.py    constants, report IDs, known frame kinds, RGB plane layout
  cfg.py         Cfg.ini parser → mapping triples + matrix coords
  keymap.py      06 04 d4 keymap-block encoder (reproduces captures exactly)
  hid.py         hidapi wrapper + known-good RGB sequence builder
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
uv run k617-ctrl inspect captures/r3.json
uv run k617-ctrl diff   captures/r2.json captures/r3.json
uv run k617-ctrl export captures/r2.json frames.json
uv run k617-ctrl replay frames.json --dry-run
```

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

* The RGB writer and any `replay` of a Restore capture **commit to flash**
  (`5AA5` magic). Do not loop them. Full-flash backup first:
  `sinowealth-kb-tool read -d redragon-k617-fizz --section full backup.hex`
* Some captures contain GET_REPORT responses (device→host). The tool only
  exports host→device SET_REPORT payloads for replay.
* The firmware has no RAM-only path — every write is a flash write.