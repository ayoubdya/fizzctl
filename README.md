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
| **Keymap Restore write on Linux** | 🔴 **under RE** — see `docs/RE_GUIDE.md` |

## Layout

```
k617_protocol.py   constants, report IDs, known frame kinds, RGB plane layout
k617_cfg.py        Cfg.ini parser → mapping triples + matrix coords
k617_hid.py        hidapi wrapper + known-good RGB sequence builder
k617_capture.py    tshark-JSON / usbmon-pcap import, diff, export
k617_remap.py      CLI entry point
data/fw-static.json  captured static-effect frames (INIT/CANVAS/ROUTING/EXEC)
docs/RE_GUIDE.md     the keymap reverse-engineering playbook
captures/            put tshark JSON exports here
```

## Quickstart

```bash
./.venv/bin/python k617_remap.py list
./.venv/bin/python k617_remap.py cfg ../redragon-k617-key-remap/Cfg.ini
./.venv/bin/python k617_remap.py inspect captures/r3.json
./.venv/bin/python k617_remap.py diff   captures/r2.json captures/r3.json
./.venv/bin/python k617_remap.py export captures/r2.json frames.json
./.venv/bin/python k617_remap.py replay frames.json --dry-run
```

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