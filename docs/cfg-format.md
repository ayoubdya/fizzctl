# `Cfg.ini` — the OEM software's export format

The official Redragon software exports a `Cfg.ini`; `fizzctl cfg` dumps it and
`KeymapEncoder` (`src/fizzctl/keymap.py`) turns it into the `06 04 d4` block.
Parser: `src/fizzctl/cfg.py`.

## Sections

`[OPT]`, `[FN]`, `[KEY]` (anything else is ignored; comments are `;`/`#`/`//`).

### `[FN] — FN-layer behaviors`

```
K15=0x04,0x22,0x00
```

3-byte behavior, per entry index (`K15` = physical key index; index space is
shared between FN and KEY — a key appears in both if it has an FN layer):

| byte0 (type) | format | meaning |
|---|---|---|
| `0x02` | `0x02, <VK>, 0x00` | FN key is a normal key (byte2 = Windows VK code) |
| `0x04` | `0x04, <consumer>, 0x00` | media key |
| `0x09` | `0x09, h, l` | special FN — bytes 2..3 are an internal command (e.g. `0x0e 0x00 0x00 0x01` = "lock Win") |

Media consumer codes (mapped to USB HID Consumer usages in `MEDIA_CODES`):

| cfg code | wire byte | name |
|---|---|---|
| `0x22` | `0xcd` | play/pause (captured in cfg_r4) |
| `0x23` | `0xb7` | stop |
| `0x24` | `0xb6` | previous |
| `0x25` | `0xb5` | next |
| `0x26` | `0xe9` | vol+ |
| `0x27` | `0xea` | vol- |
| `0x28` | `0xe2` | mute |

The cfg enum is the Redragon software's fixed list; `0x26/0x27/0x28` were
capture-confirmed and the full list cross-checks against the shared
`redragonKB-remap` enum (our cfgs only ever use `0x22/0x26/0x27/0x28`).
The wire byte is the USB HID Consumer Page usage code.

### `[KEY] — base-layer keys`

```
K15=13,51,54,79, 0x02,0x9,0x00,2,42
      ^--GUI rect--^ ^behavior^ ^matrix^
```

- **GUI rect**: `x,y,x2,y2` (pixel geometry in the OEM's editor).
- **behavior** hex: `0x02, <VK>, 0x00` (normal key, byte1 = Windows VK code;
  `0xFA` = the Fn key itself).
- **matrix** (optional tail): `col,row` hardware address.

## How a Cfg.ini becomes a keymap

`KeymapEncoder.build()`:

1. **FN-key list** = every key index that appears in `[FN]` *and* `[KEY]`,
   sorted **row-major**: rows by `geom_y // 36`, then `geom_x`.  This ordering
   drives region B/C (see `keymap.md`).
2. **Region A** (columns): `cols = {matrix_col: key}` → for each of 256 columns:
   `00 00 00 <HID>` plain, `06 00 00 <HID>` modifier, `02 00 00 <fn>` if the key
   has an FN layer, `20 00 00 00` for Fn, `00 00 00 00` empty.
3. **Regions B/C** in the same row-major list: base output (`00`/`06` + HID) in
   B, FN output in C (media `04`, special type-9 stored as 4-byte big-endian).

## The two shipped layouts (and why they differ)

- `cfgs/cfg_r2_stock.ini` — the factory Cfg captured from the OEM software.
  `src/fizzctl/stock.ini` (used by `fizzctl restore`) is a copy.  61 keys,
  28 FN keys; **Alt is a plain column-17 key**.
- `cfgs/cfg_final.ini` — the "best/most-compatible" layout derived from the
  capture set.  **Alt is FN-layered** (column 17 → FN slot 31).

The two disagree on Alt → this single fact caused the
"macro survives until you flash stock.ini, then Alt reverts to plain Alt" bug,
fixed by relocating macro bindings by physical key (`agents/keymap.md`,
`relocate_bindings` in `macro.py`).