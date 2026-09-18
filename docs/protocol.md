# Wire protocol

All traffic happens on **interface 1** (vendor page `0xFF00`, report IDs
`0x05`/`0x06`/`0x08`) via hidapi feature reports, which become USB
SET_REPORT/GET_REPORT control transfers — the exact URBs that show up in
USBPcap captures.  `src/fizzctl/hid.py` is the thin wrapper.

## Frame types

| Bytes | Name | Length | Purpose |
|---|---|---|---|
| `05 8x xx 00 00 00` (or `05 05 81 …`) | INIT | 6+ | *select* a block / start a burst |
| `06 xx xx 00 40 …` | block write | 1032 | lighting / keymap / macro body |
| `06 03 b6 00 00 … 5A A5 …` | EXEC | 1032 | commit to flash (the magic is at the end) |
| `08 0a 7a 01 …` | per-key | 382 | 96 RGB triplets, volatile |
| `05 84 d4 …` / `05 85 dc …` | read selectors | — | keymap / macro read |

The two INIT forms seen in captures: `05 05 81 00 00 00` (rgb/effect bursts)
and `05 83 b6 00 00 00` (keymap/macro bursts).  Both appear in the same write
sequence for full keymap writes (`INIT 05 05 81`, then `INIT 05 83 b6`).

## Block identities (lead bytes of the 1032-byte bodies)

| Write header | Block | Read selector |
|---|---|---|
| `06 08 b8 00 40` | MODE (effect config) | `05 88 b8 00 00 00` |
| `06 09 bc 00 40` | CANVAS (per-key RGB) | `05 89 bc 00 00 00` |
| `06 09 c0 00 40` | ROUTING (zone routing) | `05 89 c0 00 00 00` |
| `06 04 d4 00 40` | KEYMAP | `05 84 d4 00 00 00` |
| `06 05 dc 00 40` | MACRO table | `05 85 dc 00 00 00` |
| `06 03 b6 00 00` | EXEC (commit) | `05 83 b6 00 00 00` |

## Reads (the trick that unlocked everything)

To read a block: send its `05 8x xx 00 00 00` **selector**, sleep ~60 ms, then
`GET_REPORT(0x06, 1032)`.  The device answers with the block body but a
**read-flipped header** (e.g. `06 88 b8 …` instead of `06 08 b8 …`); `_read_block`
restores the normal write header so the result can be sent straight back in a
write burst.

Hardware-verified for MODE, CANVAS, ROUTING, EXEC, KEYMAP **and** MACRO.  The
keymap/macro reads return *live state* (your remaps, your macro slots) — that's
what lets every command preserve user data without a host-side state file.

Gotchas learned the hard way:

- **One interface open at a time.**  Hiding a second handle while another is
  open fails (~"open failed", looks like a permission error even with udev
  rules installed).  CLI commands are independent processes → fine.  A script
  holding the device while invoking a command that opens it again is *not*.
- Reading right after a write can return stale bytes (the flash commit is
  settling); space reads from writes, and re-read to confirm.
- Reads need the select-then-60ms-sleep cadence; skipping the sleep returns
  empty/garbage.

## Writes

`send_burst` walks the frame list, one `send_feature` per frame, sleeping
`delay_ms` between.  Two subtle behaviours:

- **Handshake (rgb/effect/key/paint):** after the first INIT the firmware
  requires a `GET_REPORT(0x06, 1032)` poll or it silently ignores the burst.
  `send_burst(…, handshake=True)` is the default and does that.
- **Keymap/macro/restore** pass `handshake=False` (their read-then-echo write
  path doesn't need it).

Order for a **keymap write**:

```
INIT 05 05 81      INIT 05 83 b6
MODE 06 08 b8 (live, kept)
CANVAS 06 09 bc (live)
ROUTING 06 09 c0 (live)
MACRO 06 05 dc     (only echoed if any slot is live)
KEYMAP 06 04 d4
EXEC 06 03 b6  (5A A5 commit)
```

Order for a **macro write**: `INIT 05 83 b6`, lighting trio, `MACRO`, `KEYMAP`,
`EXEC`.  A **paint/rgb/effect** burst: `INIT 05 05 81`, `MODE`, `CANVAS`,
`ROUTING`, `EXEC` (handshake after INIT).

The EXEC commit means **don't loop** flashes — each write is going to flash;
that's why host-side animations (`animate`) stream the volatile per-key
`08 0a 7a 01` report instead, while one-shot `paint`/`rgb` keep the CANVAS +
EXEC path.

## Per-key (volatile) path

`rgb_sequence` (used by `paint`) still writes the CANVAS block + EXEC for
one-shot board/key painting, while host-side animations (`animate`) stream the
single-report `08 0a 7a 01` path (details in `lighting.md`).