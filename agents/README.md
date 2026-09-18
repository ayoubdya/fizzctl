# K617 Fizz — reverse-engineering notes & session knowledge

Everything learned while reverse-engineering the Redragon K617 Fizz keyboard
(`fizzctl` lives in `src/fizzctl/`).  These notes capture **why** the code
does what it does — protocol facts, verified offsets, quirks, mistakes made,
and open questions.  Treat the code docstrings as the reference for the
implemented encoders; these docs give the surrounding context, especially the
non-obvious parts that only came out of live-hardware probing.

## Hardware at a glance

| | |
|---|---|
| VID / PID | `258a:0049` (Redragon / BY Tech "K617 Fizz", 60%) |
| HID interfaces | iface 0 = boot keyboard/media keys; **iface 1** = vendor page `0xFF00` with report IDs `0x05`/`0x06`/`0x08` (everything go through here) |
| Feature reports | `06 xx xx 00 40` — 1032-byte write blocks; `05 xx xx 00 00 00` — 6-byte INIT/select frames; `08 0a 7a 01` — 382-byte per-key report |
| Flash commit | the `EXEC` frame repeats a `5A A5` magic; block writes are otherwise volatile until commit |
| USB capture | USBPcap (Windows) / `tshark` JSON; `fizzctl-dev inspect|diff|export|replay` in this repo |

## Index

- [`keymap.md`](keymap.md) — `06 04 d4` keymap block: columns, FN regions, record
  types, byte aliasing, `stock.ini` vs `cfg_final.ini`, macro-binding relocation.
- [`macros.md`](macros.md) — `06 05 dc` macro table: 8×128-byte slots, event
  encoding, cycle / until-released modes, key→slot binding records, read-back.
- [`protocol.md`](protocol.md) — wire conversation: INIT/EXEC, the read
  selector(`05 8x xx`) + GET_REPORT(0x06) rows, handshake rules, write burst
  ordering.
- [`lighting.md`](lighting.md) — `06 08 b8` MODE / `06 09 bc` CANVAS / per-key
  `08` reports and the 22 firmware effects (id, color slots, speed/brightness).
- [`cfg-format.md`](cfg-format.md) — `Cfg.ini` (`[OPT]`/`[FN]`/`[KEY]`) parsed
  into the keymap encoder (geometry, matrix, behaviors, media codes).
- [`hardware-udev.md`](hardware-udev.md) — single-open HID quirk, permission
  failures, transient `open failed`, udev rules, capture workflow.
- [`re-methodology.md`](re-methodology.md) — how the protocol was cracked
  (capture diffing, live probes), hardware-verified facts, lessons, unknowns.

## Core mental model

The keyboard is a block-per-concern device:

- Four **1032-byte write blocks** echo their streaming headers on read
  (`06 08 b8`/`06 09 bc`/`06 09 c0` lighting, `06 04 d4` keymap, `06 05 dc`
  macros) — `read_keymap`/`read_macro`/`read_lighting` in `hid.py`.
- The **keymap block owns the binding record** for a macro; the **macro block
  owns the timed keystroke sequence**.
- Base-layer key output lives either in its **column record** (`8+4*col`) for
  plain keys, or in **region B** (`0x218+4*fn_index`) for keys that have an FN
  layer — the column holds a `02 00 00 <fn_index>` pointer instead.
- Because region‑B slots numerically overlap column records `132+`, a byte can
  be addressed two ways (e.g. `576` = column 142 record **and** FN slot 10).
  Layouts may move keys between "plain column" and "FN-slot" freely, which was
  the root cause of the LAlt-macro-lost-on-layout-switch bug (`relocate_bindings`).

## Practical commands (quick)

```bash
uv run fizzctl macro --read              # dump macros + which key each is bound to
uv run fizzctl keymap cfgs/cfg_final.ini # write a Cfg.ini (keeps live macros/lighting)
uv run fizzctl restore                   # factory keymap+lighting, wipes macros
uv run fizzctl setup-udev                # one-time access rules
uv run python -m unittest -v test_regression   # 21 regression tests
```

All block offsets in these docs are relative to the start of the block's own
1032 bytes (the `06 xx xx 00 40` header included at `+0x00`).