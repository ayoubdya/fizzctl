# k617-ctrl

Linux tool for controlling the Redragon K617 Fizz keyboard's RGB lighting
and restoring keymaps from `Cfg.ini` files.

## Install

```bash
# from source
uv build
uv tool install .

# or directly
pip install dist/*.whl
```

### Udev rules (needs one `sudo`)

```bash
k617-ctrl setup-udev
```

This installs `99-k617.rules` to `/etc/udev/rules.d/`, reloads udev and
re-triggers device events. The command elevates its own privileged steps
via `sudo` (you are prompted for your password), so you do **not** need to
wrap it as `sudo k617-ctrl ...` — that fails because the binary lives in a
user-local path like `~/.local/bin`. `--dry-run` prints what would be
written without touching the system. Unplug/replug the keyboard afterwards if
the trigger did not pick it up.

## Commands

| Command | Description | Persists |
|---|---|---|
| `rgb` | Set the whole board to a solid color | yes |
| `effect` | Run one of 22 firmware-native effects | yes |
| `key` | Paint a single key | yes |
| `paint` | Paint multiple keys at once | yes |
| `restore` | Restore the keymap from a Cfg.ini | yes |
| `animate` | Host-streamed animation (volatile) | no |

## Examples

All commands accept short flag aliases alongside their long forms:
| Long | Short | Applies to |
|---|---|---|
| `--color` | `-c` | `effect`, `animate` |
| `--speed` | `-s` | `effect`, `animate` |
| `--brightness` | `-b` | `effect`, `rgb` |
| `--fps` | `-f` | `animate` |

### Set the whole board to one color

```bash
k617-ctrl rgb red
k617-ctrl rgb 00ff00 --brightness 4       # or: rgb 00ff00 -b 4
```

### Firmware effects

All 22 effects from the official Redragon software are supported, with full
speed and brightness control. `speed` and `brightness` are 5 levels (0-4,
matching the firmware); higher = faster / brighter. Run `k617-ctrl effect`
with no name to list every effect:

```bash
k617-ctrl effect
k617-ctrl effect rainbow
k617-ctrl effect rainbow --speed 2 --brightness 4   # or: -s 2 -b 4
k617-ctrl effect fixed-on --color 00ff00 --brightness 3
k617-ctrl effect snake --color ff0000 --speed 4
k617-ctrl effect off
```

Aliases are supported: `static` → `fixed-on`, `wheel` → `rainbow-wheel`,
`snake` → `retro-snake`, `waterfall` → `colorful-waterfall`.

### Per-key painting

Keys are named by their top-left legend. Any key *not* listed turns off
because the canvas is absolute:

```bash
k617-ctrl key W ff0000
k617-ctrl paint W=ff0000 A=00ff00 S=ffff00 D=ff00ff Space=ffffff
```

### Host-side animations

Volatile — lost when the keyboard reconnects or reboots. Run `k617-ctrl
animate` with no name to list the animations:

```bash
k617-ctrl animate
k617-ctrl animate rainbow --fps 30 --duration 10  # or: -f 30
k617-ctrl animate chase --color ff0000 --speed 2  # or: -c yellow -s 2
k617-ctrl animate solid --color 0000ff
```

### Restore keymap

Write a full keymap from a `Cfg.ini` to flash:

```bash
k617-ctrl restore Cfg.ini
```

### Debug mode

```bash
k617-ctrl --debug effect rainbow
```

Shows the raw frames and handshake bytes sent over HID.

## Dev tools

The `k617-ctrl-dev` binary exposes the full reverse-engineering toolkit
(capture inspect, diff, export, replay) in addition to all user commands.

```bash
k617-ctrl-dev inspect captures/r3.json
k617-ctrl-dev diff captures/r2.json captures/r3.json
k617-ctrl-dev export captures/r2.json frames.json
k617-ctrl-dev replay frames.json --dry-run
k617-ctrl-dev cfg Cfg.ini
k617-ctrl-dev list
```

## How the RGB paths work

**Flash writes** (`rgb`, `key`, `paint`, `effect`, `restore`) send a 4-5 frame
burst through the vendor HID interface (`258a:0049`, interface 1, usage page
`0xFF00`). The sequence ends with a `5AA5` magic commit that writes to flash.
Colors persist across reboots. Keys not listed in a `key` or `paint` canvas
turn off.

**Animations** (`animate`) stream 382-byte per-key reports at the requested
frame rate with no flash commit. They are host-side only and lost on
reconnect.

## Colors

Colors can be given as a name or `RRGGBB` hex (with or without `#`). Spaces,
dashes and underscores in names are ignored, so `light green`, `light-green`
and `lightgreen` are equivalent:

```text
red green blue white black off
yellow cyan magenta orange pink purple lime teal violet indigo coral salmon
navy brown gold silver gray grey olive maroon
lightred lightgreen lightblue darkred darkgreen darkblue
```

Both `-c yellow` and `--color ffaa00` work anywhere a color flag is accepted.
