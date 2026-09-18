# Keymap block (`06 04 d4`)

1032 bytes total.  The block assigns, for every physical key, what the key
sends; the keymap encoder is `src/fizzctl/keymap.py`, the raw data the code
starts from is `blobs.CONST_KEYMAP` (a factory capture).

## Regions

All offsets are from the block start:

| Region | Offset | Size | Meaning |
|---|---|---|---|
| header | `+0x00` | 8 B | `06 04 d4 00 40 00 00 00` |
| **A — column records** | `+0x08` | 0x210 (132×4) | `offset = 8 + 4*col`, real matrix columns 0..131 |
| **B — FN base outputs** | `+0x218` | up to 0xC0 | `offset = 0x218 + 4*fn_index` — the *base layer* of keys that have an FN layer |
| **C — FN layer outputs** | `+0x2d8` | up to 0xC0 | `offset = 0x2d8 + 4*fn_index` — what the same keys send while FN is held |

`region B` and `region C` entries are written in a **row-major** list of the
FN-capable keys: rows bottom of GUI → bottom of keyboard, bucketed by
`geom_y // 36`, then by `geom_x`.  The `fn_index` in the column pointer is the
0-based position of that key inside this same list.

Because the three regions are concatenated, the column-record addressing only
exists for columns 0..131; offsets ≥ `0x218` coincide with columns `132+`
(see aliasing below).

## 4-byte record types

Where a record lives determines what it means:

| byte0 | Meaning |
|---|---|
| `00 00 00 <HID>` | plain key (column record or region-B/C output) |
| `06 00 00 <HID>` | modifier (e.g. LAlt) |
| `02 00 00 <fn>` | has an FN layer → column points at FN slot `<fn>` |
| `20 00 00 00` | the `Fn` key itself |
| `10 00 <mode> <slot>` | **macro binding** (see `macros.md`) |
| `04 00 00 <media>` | media key (region C) |
| `00 00 00 00` | unassigned / empty |

HID = USB HID keyboard usage code (`VK2HID` in `keymap.py` maps Windows VK
codes to them).

## Byte aliasing — the trap

Column records only cover real columns 0..131 (`+0x08 .. +0x217`).  Anything
beyond that is region B/C, but numerically it still *is* a 4-byte slot at
`8 + 4*col` for col ≥ 132:

```
offset 576 = 0x218 + 4*10   (region B, FN slot 10)  ==  column 142's record
offset 660 = 0x218 + 4*31   (region B, FN slot 31)  ==  column 163's record
```

So a bound/hidden byte can be "read" under two different names.  Concretely,
key "0" outputs via FN slot 10 (`column 61` holds `02 00 00 0a`), so its output
byte 576 shows up as "column 142" if you scan by column offset.  Any code that
"finds the column of offset X" must treat `X ≥ 0x218` as FN-storage, not as a
real column, and resolve the actual column by *scanning for the `02 00 00 <fp>`
pointer* (`relocate_bindings` in `macro.py` does exactly this).

## Verified offsets (hardware)

| Key | Offset | What it is |
|---|---|---|
| "0" | 576 | FN slot 10 output (`0x218 + 0x28`); column 61 pointer `02 00 00 0a` |
| CapsLock | 616 | FN slot 20 output (`0x218 + 0x50`) |
| LAlt | 660 | FN slot 31 output (`0x218 + 0x7c`) in `cfg_final.ini`/CONST |
| LAlt | 76  | **plain** column-17 record in `stock.ini` (`8 + 4*17`) |

## Layouts differ — and that broke raw-offset copying

Two shipped layouts disagree on where Alt lives:

- `cfgs/cfg_final.ini` (≈ `blobs.CONST_KEYMAP`): LAlt is **FN-layered**;
  column 17 = `02 00 00 1f` (pointer to slot 31), region B slot 31 @660 =
  `06 00 00 e2`.  61 real keys spread across columns; 33 FN-capable keys.
- `cfgs/cfg_r2_stock.ini` (= packaged `src/fizzctl/stock.ini`, used by
  `restore`): LAlt is a **plain** column-17 key; column 17 = `06 00 00 e2`
  @76.  Only 28 FN keys — **FN slot 31 does not exist**, byte 660 is unused.

Bug (fixed in `relocate_bindings`): macros were previously "kept" across a
keymap write by copying the live `10 00 <mode> <slot>` binding at its **raw
offset**.  Bind one under `cfg_final` → LAlt binding sits at 660; flash
`stock.ini` → 660 is dead (nothing points at FN slot 31), LAlt "just gives
normal alt".  Key "0" "worked" by luck: its byte 576 is shared by both layouts.

### The fix — relocate by physical key, not offset

For every binding `(off, mode, slot)` read off the live keymap:

1. **Plain key** (`off < 0x218`): the physical column is `col = (off - 8) / 4`.
2. **FN-slot binding** (`off ≥ 0x218`): `fp = (off - 0x218) / 4`; find the one
   column whose live record is `02 00 00 fp` → that's the physical column.
   (If none — an *orphan* left behind by the old copy-at-offset bug — fall back
   to the `CONST_KEYMAP` oracle: look up the HID at `off` in the stock capture,
   then `locate_key(target, hid)`.)
3. **Place in the target**: look at the column's record in the *new* layout —
   `02 00 00 <fp'>` → region B slot `fp'`; `00/06` plain → the column record.
4. Round-trip off/on `cfg_final` ↔ `stock.ini` is verified in
   `test_relocate_bindings_across_layouts` / `..._orphan_fallback`.

## Reading it back (living device)

`read_keymap` (`05 84 d4` selector) returns the **live base layer**: current
Cfg.ini remaps are echoed verbatim.  The exception is a bound macro key, whose
original output is **not recoverable** — the device echoes the binding record
`10 00 <mode> <slot>` in its place.  Understanding a binding therefore needs
the `CONST_KEYMAP` oracle to translate offset → HID → key name (this is what
`fizzctl macro --read` uses, `_binding_for_hid` / `cmd_macro_read`).