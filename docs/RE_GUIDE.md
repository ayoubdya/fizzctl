# Reverse-Engineering the K617 "Restore" (keymap write) protocol

Goal: replicate the OEM software's **Restore** button on Linux so the `Cfg.ini`
keymap can be flashed to the keyboard's onboard memory without Windows.

We already own two sibling protocols for this keyboard's vendor interface
(`258a:0049`, interface 1, usage page `0xFF00`):

| Protocol | Report | Frames | Status |
|---|---|---|---|
| Static RGB write (k617-fizz) | `0x06` 1032 B | INIT(05) → GET_REPORT → CANVAS(06 09 bc) → ROUTING(06 09 c0) → EXEC(06 03 b6, `5AA5` commit) | ✅ decoded |
| Sinodragon per-key (fizz-rgb) | `0x08` 382 B | single frame, header `08 0a 7a 01`, 96 RGB triplets | ✅ decoded |
| **Keymap / Restore** | **unknown** | **unknown** | ❌ this doc |

Every write to this keyboard ends in a flash commit (`5AA5` in the EXEC block),
so Restore almost certainly uses the same envelope: *some number of data blocks
+ an EXEC-style commit*. The unknown is the **data block layout** for the
`[KEY]`/`[FN]` tables.

---

## 1. What to capture and how

Run the OEM software in a real Windows boot (or a VM with the keyboard passed
through). Capture the USB traffic with **USBPcap** (ships with the Wireshark
installer) + **Wireshark**.

> The Restore sequence is a handful of HID control transfers. Every capture
> below is tiny (a few KB). Wireshark reassembles the 1032-byte feature
> reports into single frames — enable *Preferences → Protocols → USB →
> "Reassemble USB transfers"* so `usb.data_fragment` holds the full payload.

Recommended capture plan (five runs of ~30 seconds each):

| Run | What you press | Why |
|---|---|---|
| R1 | Open software, do nothing | Startup read (GET_REPORTs the firmware sends when poked) |
| R2 | **Restore** with a stock `Cfg.ini` | The baseline Restore sequence |
| R3 | Change **one base-layer key** (e.g. Right Shift → `F12`), **Restore** | Isolates that one key's bytes |
| R4 | Change **one FN-layer mapping** (e.g. FN+Tab → Play/Pause), **Restore** | Isolates the FN table + how FK tables travel |
| R5 | Change two unrelated keys at once, **Restore** | Confirms entries are position-independent |

Pro tip: back up the stock `Cfg.ini` before R2 (Windows side), and keep the
modified `.ini` files side by side so each capture is traceable to its config.

On Windows, export each capture to JSON for analysis:

```
& "C:\Program Files\Wireshark\tshark.exe" -r r3.pcapng -T json > r3.json
```

If Wireshark's USB reassembly ever fails, capture at 64-byte granularity and
filter/merge on Linux instead — the tool's `inspect` output shows the fragment
sizes so you'll see it immediately.

---

## 2. First-pass analysis (no understanding required)

```
python3 k617_remap.py inspect r2.json     # what packets went from host→device
python3 k617_remap.py diff  r2.json r3.json   # the one-key change
```

Expected result of `diff r2 r3`:
* INIT and ROUTING/commit frames unchanged (or trivially different),
* the **data-block bytes for Right Shift change to `02 3C 00`** (`0x3C` = F12
  Virtual-Key code) at some offset,
* everything else unchanged → the keymap block has fixed slots per key.

That single diff **answers the layout question**: offset-of-key ↔ byte-slot.
Repeat for R4 to learn how the FN table is encoded (is `K15=04 22 00` a new
entry appended, or does FN-press-identity live in the same table?).

> Reading tip: `Virtual-Key codes` — `0x26` up, `0x27` right, `0x28` down,
> `0xA3` RCtrl, `0xA5` RAlt, `0xFA` FN, `0x5D` App/Menu. Media codes are the
> `0x04` table (play `0x22`, vol− `0x27`, vol+ `0x26`, mute `0x28`, prev
> `0x24`, next `0x25`).

---

## 3. Decoding the keymap block

With R2→R3→R5 you know the byte slot for each key. Now encode the mapping
triples from `Cfg.ini` into each slot and figure out the container format:

```
[KEY] K53 = ... 0x02, 0x26, 0x00, 82, 97        # Right Shift → Up
[FN]  K15 = 0x04, 0x22, 0x00                    # FN+Tab → Play/Pause
```

Questions to answer from the diffs:

1. **Slot size** — 3 bytes per behavior triple, or padded to 4/8?
2. **Order** — is slot order by matrix column (`Cfg.ini` order) or by LED index?
3. **Where the routing/matrix numbers go** — R3 showed only the behavior bytes
   differ, so matrix is either fixed in firmware or lives in the unchanged
   ROUTING-style frame. If the ROUTING block *also* changes when a key moves,
   that's keymap data too.
4. **How FN identities are tagged** — is `K59` (the FN physical key) flagged
   with `0xFA` in the base table and the `[FN]` entries stored in a second
   table? Captures R4 will tell.
5. **Multi-byte values** — custom functions like `09 00 0b000300` are 4-byte
   little-endian payloads; check endianness against the captured bytes.

The diff offsets literally are the answer sheet — no blind guessing.

---

## 4. Cross-validation: differential flash dump (the no-guess check)

You already have a pristine full-flash dump of the **stock** keyboard at
`~/k617-backup/k617-full-backup.hex`. Use it as the `before`:

1. Apply the keymap in Windows (R3's config), boot back to Linux.
2. `sinowealth-kb-tool read -d redragon-k617-fizz --section full after.hex`
3. Byte-compare `before.hex` vs `after.hex`:

```bash
python3 - <<'PY'
a = bytes(open('/home/microgod/k617-backup/k617-full-backup.hex').read(), 'ascii')
b = open('/home/microgod/k617-backup/after.hex').read()  # or use the .hex convert
# simplest: compare the intel-hex files with a small script, or convert to bin
# with: objcopy -I ihex -O binary before.hex before.bin
PY
```

(tip: `objcopy --input-target ihex --output-target binary` converts the Hex
files, then `cmp -l before.bin after.bin` lists changed offsets.)

What you learn:
* the **flash offset** of the keymap region (constant for your unit),
* the **on-flash encoding** (may differ from the on-wire encoding if the
  firmware marshals data — itself a useful fact),
* whether the top-level Cfg sections map to distinct flash regions.

Optional long-game: single-key-change flash dumps let you build a flash→key
map and even write the keymap *directly* into flash with
`sinowealth-kb-tool write` — a fallback if the on-wire protocol stays opaque.

**Risk notes:** keep the stock backup safe; a `write` only touches the region
you patch — never touch the bootloader section (first `bootloader_size`,
default 4096 bytes) or the `0x0001-0x0002` vector area on SH68F90.

---

## 5. From decode to Linux writer

Once the block layout is known, the Linux tool has a clean path (already
scaffolded):

1. `k617_cfg.py` parses `[KEY]`/`[FN]` → mapping triples.
2. Build the data block from `Cfg.ini`, patch it into the captured Restore
   template (R2's frames with INIT/ROUTING/EXEC kept verbatim).
3. `k617_remap.py replay frames.json` sends the sequence over hidapi — same
   code path already validated on the RGB writer (which uses the same
   feature-report transport and the same `5AA5` commit handshake).

When that round-trips, the keymap lives onboard and follows the keyboard to
any OS — exactly what the Windows software does, minus Windows.

---

## 6. Safety rules (same as the RGB RE work)

* Your full-flash backup is the ground truth. Before any experimental write,
  take a fresh `read --section full`.
* HID captures are read-only; `replay`/`rgb` write to flash. Never loop them.
* Keep a working USB keyboard around while experimenting — a botched keymap
  write can map *your* Esc to nothing.
* Reset to factory anytime with **FN+ESC** (wipes the keymap + RGB profiles).
* Restore 3.9: if a commit looks wrong mid-sequence, unplug immediately —
  the firmware only commits at the EXEC/`5AA5` frame.