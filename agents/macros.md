# Macro block (`06 05 dc`)

1032 bytes: the 5-byte header `06 05 dc 00 40` plus **8 slots of 128 bytes**,
slot *s* at `9 + 128*s` (slot0 @9, slot1 @137, …).  Encoder/decoder live in
`src/fizzctl/macro.py`.

## A slot

A 128-byte slot is a 1-byte **cycle count** followed by **2-byte events**:

```
slot[0]        cycles (how many times the sequence plays on a key press,
               for MODE_CYCLES macros; ignored-ish for until-released, the
               firmware header still stores a number there)
slot[1..]      events, packed 2 bytes each until a zero byte
   byte0  = delay_ms (0..127) | 0x80 when this event RELEASES the key
   byte1  = USB HID keyboard usage code
```

Example (the ABC macro that ships with the keyboard, decoded by
`fizzctl macro --read`):

```
94AP 16AR 93BP 32BR 78CP 3CR
```

i.e. events `(94, A, press) (16, A, release) (93, B, press) (32, B, release)
(78, C, press) (3, C, release)` — presses and a release share the same byte0,
with bit 7 (`0x80`) meaning *release*; delay is `byte0 & 0x7F`.

The OEM "newmacro" (typing *rgb*) was caught as
`109 P-R 16 P-G 63 R-R 93 R-G 63 P-B 3 R-B` (delays are per-event and vary,
so the numeric prefix in `--read` output is a per-event delay, not a
constant).

## Key → slot binding (and why it's NOT in this block)

The macro-to-key binding lives in the **keymap block**, not the macro block.
`bind_macro` turns the bound key's record into:

```
10 00 <mode> <slot>
   mode 0x01  = MODE_CYCLES        play `cycles` times per press
   mode 0x04  = MODE_UNTIL_RELEASED cycle until the bound key is released
   slot       = 0-based macro slot this key triggers
```

The record is written at the key's **output position**: its column record
(`8+4*col`) for plain keys, or region-B FN slot (`0x218+4*fp`, via the column's
`02 00 00 <fp>` pointer) for FN-layered keys.  Verified live offsets:
"0"→576, CapsLock→616, LAlt→660 (FN slots 10/20/31 under `cfg_final`).

Both modes are visible per binding in `fizzctl macro --read`:
`slot1: cycles=1  bound to: 0 (until released)` — until-released macros are
flagged explicitly because their header says `cycles=1` anyway, which alone is
ambiguous.

## Reading it back (live)

`read_macro` (`05 85 dc` selector, GET_REPORT(0x06, 1032)) returns the live
table with every stored slot intact — **hardware-verified**, same find as for
the keymap.  An empty slot is all-zero; `slots_in_frame`/`decode_macro_frame`
skip those.

Because macros+slots are read before every write, `fizzctl macro` can keep
existing macros without any host-side state file (`state.py` was deleted once
this worked).  The device is the source of truth.

## Writing

`build_macro_frame`+`encode_slot` build the block; a macro write burst is:

```
INIT 05 83 b6        → MODE 06 08 b8   (live lighting, kept)
                     → CANVAS 06 09 bc (live)
                     → ROUTING 06 09 c0(live)
                     → MACRO 06 05 dc  (full table incl. all slots)
                     → KEYMAP 06 04 d4 (all bindings + current remaps)
                     → EXEC 06 03 b6   (5AA5 commit)
```

Everything is re-read first (lighting, keymap, macro table) and echoed back so
one macro bind never clobbers another macro, a binding, or the live effect.

## Reserved quirks

- Max slot delay is 127 ms (7 bits).  `text_events` maps each typed char to a
  press event + a release event, so *n* characters = 2*n events.
- `--remove-all` (a `macro` sub-flag) writes an *empty* macro table and restores
  every binding record from the `CONST_KEYMAP` oracle.  A second run reports
  `no macros to remove`.
- Slots are 0-based; the OEM software numbers them 1-based in its UI — mapping
  between the two has bitten before.