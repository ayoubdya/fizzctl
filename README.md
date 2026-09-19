# fizzctl

Control a Redragon K617 Fizz keyboard on Linux: solid colors, 22 firmware-native
RGB effects, per-key painting, live animations, on-device key macros with a
readable back, and `Cfg.ini` keymap restore — no vendor software required.

## Install

```bash
# from PyPI
pip install fizzctl

# from PyPI (uv)
uv tool install fizzctl

# from source
git clone https://github.com/you/fizzctl.git && cd fizzctl
uv build && uv tool install .
```

### Udev rules (needs one `sudo`)

```bash
fizzctl setup-udev
```

This installs `99-k617.rules` to `/etc/udev/rules.d/`, reloads udev and
re-triggers device events. The command elevates its own privileged steps
via `sudo` (you are prompted for your password), so you do **not** need to
wrap it as `sudo fizzctl ...` — that fails because the binary lives in a
user-local path like `~/.local/bin`. Unplug/replug the keyboard afterwards if
the trigger did not pick it up.

## Commands

| Command | Description | Persists |
|---|---|---|
| `rgb` | Set the whole board to a solid color | yes |
| `effect` | Run one of 22 firmware-native effects | yes |
| `key` | Paint a single key | yes |
| `paint` | Paint multiple keys at once | yes |
| `keymap` | Write the keymap from a Cfg.ini | yes |
| `macro` | Bind a key that types text, `--remove-all`, or `--read` | yes |
| `restore` | Reset to the factory keymap, lighting and macros | yes |
| `animate` | Host-streamed animation (volatile) | no |

## Examples

All commands accept short flag aliases alongside their long forms:
| Long | Short | Applies to |
|---|---|---|
| `--color` | `-c` | `effect`, `animate` |
| `--speed` | `-s` | `effect`, `animate` |
| `--brightness` | `-b` | `effect`, `rgb` |
| `--fps` | `-f` | `animate` |

### Write keymap (most important)

Apply the full keymap (bindings, lighting zones, function keys) from a
`Cfg.ini` — the file the official Redragon software exports:

```bash
fizzctl keymap Cfg.ini
```

- A `keymap` write keeps your current effect/color/brightness and any macros
  you have bound.
- `fizzctl restore` does the opposite: it resets the keyboard to factory
  state by writing the packaged stock keymap and lighting and wiping every
  macro.

### Macros

Bind a key to a macro that types text. Each macro gets its own slot (up to
8), and previously bound macros are kept — the device's current keymap is
read and reused as the base, and your lighting is untouched:

```bash
fizzctl macro --key CapsLock rgb          # type "rgb" each press
fizzctl macro --key LAlt --delay-ms 50 --cycles 3 hello
fizzctl macro --key 2 --until-released aaaa
```

Options: `--delay-ms` (default 30) is the delay between typed events,
`--cycles` (default 1) plays the macro that many times per press, and
`--until-released` types in a loop until the key is let go. Macros are read
straight back off the keyboard's flash: each write reads the current keymap
and macro table first, patches them, and writes the full table back — so
later macro/keymap writes never drop your existing macros, and nothing is
saved on the host. `fizzctl macro --read` dumps the on-device table back,
each slot shown with the key(s) bound to it and whether it repeats until the
key is released. To undo:

```bash
fizzctl macro --remove-all          # unbind every macro, keep keymap + lighting
fizzctl restore                     # full factory reset (keymap, lighting, macros)
```

### Set the whole board to one color

```bash
fizzctl rgb red
fizzctl rgb 00ff00 --brightness 4       # or: rgb 00ff00 -b 4
```

### Firmware effects

All 22 effects from the official Redragon software are supported, with full
speed and brightness control. `speed` is 1..5 and `brightness` is 0..4 (5
levels each, matching the firmware); higher = faster / brighter. Run
`fizzctl effect` with no name to list every effect:

```bash
fizzctl effect
fizzctl effect rainbow
fizzctl effect rainbow --speed 2 --brightness 4   # or: -s 2 -b 4
fizzctl effect fixed-on --color 00ff00 --brightness 3
fizzctl effect snake --color ff0000 --speed 4
fizzctl effect off
```

Aliases are supported: `static` → `fixed-on`, `wheel` → `rainbow-wheel`,
`snake` → `retro-snake`, `waterfall` → `colorful-waterfall`.

### Per-key painting

Keys are named by their top-left legend. Any key *not* listed turns off
because the canvas is absolute:

```bash
fizzctl key W ff0000
fizzctl paint W=ff0000 A=00ff00 S=ffff00 D=ff00ff Space=ffffff
```

### Host-side animations

Volatile — lost when the keyboard reconnects or reboots. Run `fizzctl
animate` with no name to list the animations:

```bash
fizzctl animate
fizzctl animate rainbow --fps 30 --duration 10  # or: -f 30
fizzctl animate chase --color ff0000 --speed 2  # or: -c yellow -s 2
fizzctl animate solid --color 0000ff
fizzctl animate chase --daemon     # free the terminal; daemon streams until stopped
fizzctl animate stop               # stop the daemon
```

### Debug mode

```bash
fizzctl --debug effect rainbow
```

Shows the raw frames and handshake bytes sent over HID.

## Dev tools

The `fizzctl-dev` binary exposes the full reverse-engineering toolkit
(capture inspect, diff, export, replay) in addition to all user commands.

```bash
fizzctl-dev inspect captures/r3.json
fizzctl-dev diff captures/r2.json captures/r3.json
fizzctl-dev export captures/r2.json frames.json
fizzctl-dev replay frames.json
fizzctl-dev cfg Cfg.ini
fizzctl-dev list
```

## How the RGB paths work

**Flash writes** (`rgb`, `key`, `paint`, `effect`, `keymap`, `macro`,
`restore`) send a burst through the vendor HID interface (`258a:0049`,
interface 1, usage page `0xFF00`). The sequence ends with a `5AA5` magic
commit that writes to flash. Colors persist across reboots. Keys not listed
in a `key` or `paint` canvas turn off.

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
