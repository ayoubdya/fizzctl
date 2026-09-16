"""k617-ctrl — command-line interface.

User commands (``k617-ctrl``):
    k617-ctrl rgb <color> [--brightness N]  # whole-board solid color (flash write)
    k617-ctrl effect <name>            # firmware-native effect (FLASH WRITE)
    k617-ctrl key <key> <color>        # paint one key (FLASH WRITE)
    k617-ctrl paint <key>=<color>...   # paint many keys (FLASH WRITE)
    k617-ctrl animate <name>           # host-side animation (volatile stream)
    k617-ctrl restore <Cfg.ini>        # full keymap Restore (FLASH WRITE)
    k617-ctrl setup-udev               # install 99-k617.rules (needs root)

Dev commands (``k617-ctrl-dev``, reverse-engineering toolkit):
    k617-ctrl-dev list
    k617-ctrl-dev cfg <Cfg.ini>
    k617-ctrl-dev inspect <cap.json>
    k617-ctrl-dev diff <capA.json> <capB.json>
    k617-ctrl-dev export <cap.json> <frames.json>
    k617-ctrl-dev replay <frames.json> [--dry-run] [--delay-ms N]
    plus every user command above
"""
from __future__ import annotations

import argparse
import sys

from .animations import cmd_animate
from .capture import diff_captures, export_frames, load_frames, load_tshark_json, significant
from .cfg import CfgIni
from .hid import K617, NoDeviceError
from .keymap import KeymapEncoder
from .protocol import RESTORE_CONSTANT_FRAMES


def cmd_list(_):
    import hid
    from .protocol import PID, VID

    for d in hid.enumerate(VID, PID):
        print(d["path"], "iface", d.get("interface_number"), "usage", hex(d.get("usage_page", 0)))
    if not hid.enumerate(VID, PID):
        print("no K617 found (is it plugged in?)")


def cmd_cfg(args):
    cfg = CfgIni(args.cfg)
    print(f"[FN] {len(cfg.fn)} entries")
    for idx, be in cfg.fn_entries:
        print(f"  K{idx:<3} {', '.join(f'0x{b:02X}' for b in be)}")
    print(f"[KEY] {len(cfg.keys)} entries")
    for idx, e in cfg.key_entries:
        print(f"  K{idx:<3} matrix={e.matrix} {', '.join(f'0x{b:02X}' for b in e.behavior)}")


def cmd_inspect(args):
    recs = load_tshark_json(args.capture)
    out = significant(recs)
    print(f"{len(recs)} USB records, {len(out)} host->device writes")
    for r in out:
        print(" ", r)


def cmd_diff(args):
    a = load_tshark_json(args.a)
    b = load_tshark_json(args.b)
    changes = diff_captures(a, b)
    print(f"aligned {len(significant(a))} vs {len(significant(b))} writes; {len(changes)} differ")
    for c in changes:
        print(f"\nframe {c['frame']}: {c['kind_a']} ({c['len_a']}B) -> {c['kind_b']} ({c['len_b']}B), "
              f"{c['diff_count']} bytes differ")
        for off, ca, cb in c["offsets"]:
            print(f"   +{off:#06x}  {ca:>2} -> {cb:>2}")
    if not changes:
        print("no differences — both captures identical")


def cmd_export(args):
    recs = load_tshark_json(args.capture)
    export_frames(recs, args.out)


def cmd_replay(args):
    frames = load_frames(args.frames)
    print(f"{len(frames)} frames loaded from {args.frames}")
    for i, f in enumerate(frames):
        from .protocol import frame_kind
        print(f"  {i}: {frame_kind(f)} ({len(f)}B)")
    try:
        dev = K617(dry_run=args.dry_run, debug=args.debug)
    except NoDeviceError as e:
        print(f"error: {e}")
        return 1
    try:
        dev.send_sequence(frames, delay_ms=args.delay_ms)
    finally:
        dev.close()
    return 0


def cmd_restore(args):
    cfg = CfgIni(args.cfg)
    keymap = KeymapEncoder(cfg).build()
    # exact capture order: INIT, INIT, MODE, CANVAS, ROUTING, KEYMAP, EXEC
    frames = [
        bytes.fromhex("050581000000"),       # INIT
        bytes.fromhex("0583b6000000"),       # INIT
        RESTORE_CONSTANT_FRAMES[0],          # MODE
        RESTORE_CONSTANT_FRAMES[1],          # CANVAS
        RESTORE_CONSTANT_FRAMES[2],          # ROUTING
        keymap,                               # 06 04 d4 keymap block
        RESTORE_CONSTANT_FRAMES[3],          # EXEC (5AA5 commit)
    ]
    if args.debug:
        print(f"built {len(frames)} frames from {args.cfg}")
        print(f"  keymap block: {len(keymap)}B, must equal 1032")
    if len(keymap) != 1032:
        print("error: keymap block is not 1032 bytes")
        return 1
    if args.dry_run:
        if args.debug:
            for i, f in enumerate(frames):
                print(f"  [{i + 1}/{len(frames)}] {_kind(f)} ({len(f)}B)")
        else:
            print(f"[dry-run] would restore keymap from {args.cfg}")
        return 0
    try:
        dev = K617(debug=args.debug)
    except NoDeviceError as e:
        print(f"error: {e}")
        return 1
    try:
        dev.send_sequence(frames, delay_ms=args.delay_ms)
    finally:
        dev.close()
    print(f"restored keymap from {args.cfg}")
    return 0


def cmd_rgb(args):
    """Shortcut for `effect fixed-on <color>` (whole-board solid color).

    Examples:
        k617-ctrl rgb ff0000
        k617-ctrl rgb 00ff00 --brightness 4
    """
    args.name = "fixed-on"
    args.speed = None
    return cmd_effect(args)


def cmd_effect(args):
    """Run a firmware-native effect (flash write).

    Examples:
        k617-ctrl effect rainbow                 # default speed/brightness
        k617-ctrl effect rainbow --speed 2 --brightness 4
        k617-ctrl effect waterfall --color ff8800
        k617-ctrl effect static --brightness 1
    """
    from .effects import EFFECT_ACCEPTS_COLOR, EFFECT_DEFAULTS, EFFECT_ID, encode_firmware_effect
    from .hid import send_firmware_effect

    name = args.name
    if name not in EFFECT_ID:
        print(f"unknown effect {name!r}. Available ({', '.join(EFFECT_ID)}):")
        for n, eid in EFFECT_ID.items():
            defs = EFFECT_DEFAULTS[n]
            color = "yes" if n in EFFECT_ACCEPTS_COLOR else "-"
            print(f"  {n:18s} id={eid:#04x} color:{color:3s} default sb={defs[0]}.{defs[1]}")
        return 1

    color = None
    if args.color:
        color = parse_color(args.color)
        if color is None:
            print(f"bad color {args.color!r}: use a name or hex")
            return 1

    frames = encode_firmware_effect(name, color, speed=args.speed, brightness=args.brightness)
    if args.dry_run:
        if args.debug:
            for i, f in enumerate(frames):
                print(f"  [{i + 1}/5] {_kind(f)} ({len(f)}B)")
        else:
            print(f"[dry-run] would apply effect {name!r}")
        return 0
    try:
        dev = K617(debug=args.debug)
    except NoDeviceError as e:
        print(f"error: {e}")
        return 1
    try:
        send_firmware_effect(dev, frames)
    finally:
        dev.close()
    desc = f"effect {name}"
    if color:
        desc += f" (color=#{color[0]:02x}{color[1]:02x}{color[2]:02x})"
    if args.speed is not None or args.brightness is not None:
        desc += f" (speed={args.speed}, brightness={args.brightness})"
    print(f"applied {desc}")
    return 0


def cmd_key(args):
    """Paint a single key via the CANVAS + 5AA5 execute path (flash write),
    persistent across reboots. Equivalent to k617-fizz `send_colors`."""
    from .hid import rgb_sequence, send_rgb
    from .protocol import NAME_TO_INDEX

    color = parse_color(args.color)
    if color is None:
        print(f"bad color {args.color!r}")
        return 1
    if args.key not in NAME_TO_INDEX:
        print(f"unknown key {args.key!r}. Available: {', '.join(sorted(NAME_TO_INDEX))}")
        return 1
    frames = rgb_sequence({args.key: color})
    if args.dry_run:
        if args.debug:
            print(f"[dry-run] {len(frames)} frames, would paint {args.key} -> {color}")
        else:
            print(f"[dry-run] would paint {args.key} -> {color}")
        return 0
    try:
        dev = K617(debug=args.debug)
    except NoDeviceError as e:
        print(f"error: {e}")
        return 1
    try:
        send_rgb(dev, frames)
        print(f"painted {args.key} -> #{color[0]:02x}{color[1]:02x}{color[2]:02x}")
    finally:
        dev.close()
    return 0


def cmd_paint(args):
    """Paint many keys via the CANVAS + 5AA5 execute path (flash write):
    k617-ctrl paint W=ff0000 A=00ff00 S=0000ff D=ffffff
    """
    from .hid import rgb_sequence, send_rgb
    from .protocol import NAME_TO_INDEX

    colors = {}
    for spec in args.specs:
        if "=" not in spec:
            print(f"bad spec {spec!r}: expected KEY=COLOR")
            return 1
        key, c = spec.split("=", 1)
        if key not in NAME_TO_INDEX:
            print(f"unknown key {key!r}")
            return 1
        color = parse_color(c)
        if color is None:
            print(f"bad color {c!r}")
            return 1
        colors[key] = color
    frames = rgb_sequence(colors)
    if args.dry_run:
        if args.debug:
            print(f"[dry-run] {len(frames)} frames, would paint {len(colors)} keys -> {colors}")
        else:
            print(f"[dry-run] would paint {len(colors)} keys")
        return 0
    try:
        dev = K617(debug=args.debug)
    except NoDeviceError as e:
        print(f"error: {e}")
        return 1
    try:
        send_rgb(dev, frames)
        print(f"painted {len(colors)} keys")
    finally:
        dev.close()
    return 0


def parse_color(s: str) -> tuple[int, int, int] | None:
    from .effects import parse_color as _pc

    return _pc(s)


def _kind(frame: bytes) -> str:
    from .protocol import frame_kind

    return frame_kind(frame)


def _build_parser(dev: bool) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="k617-ctrl" if not dev else "k617-ctrl-dev",
        description="Redragon K617 Fizz controller"
        if not dev
        else "Redragon K617 reverse-engineering toolkit (dev)",
    )
    p.add_argument("--debug", action="store_true", help="verbose frame-level logging")
    sub = p.add_subparsers(dest="cmd", required=True)

    if dev:
        sub.add_parser("list", help="list K617 HID interfaces")
        pc = sub.add_parser("cfg", help="parse and dump a Cfg.ini")
        pc.add_argument("cfg")
        pi = sub.add_parser("inspect", help="summarise a tshark JSON capture")
        pi.add_argument("capture")
        pd = sub.add_parser("diff", help="diff two tshark JSON captures")
        pd.add_argument("a")
        pd.add_argument("b")
        pe = sub.add_parser("export", help="extract host->device writes to frames.json")
        pe.add_argument("capture")
        pe.add_argument("out")
        pr = sub.add_parser("replay", help="replay exported frames via hidapi")
        pr.add_argument("frames")
        pr.add_argument("--dry-run", action="store_true")
        pr.add_argument("--delay-ms", type=int, default=30)

    px = sub.add_parser("rgb", help="whole-board solid color (shortcut for `effect fixed-on`)")
    px.add_argument("color")
    px.add_argument("--brightness", type=int, help="0..15 nibble")
    px.add_argument("--dry-run", action="store_true")

    peff = sub.add_parser("effect", help="run a firmware-native effect (flash write)")
    peff.add_argument("name")
    peff.add_argument("--color", help="base color (name or hex) — only for color-capable effects")
    peff.add_argument("--speed", type=int, help="0..15 nibble")
    peff.add_argument("--brightness", type=int, help="0..15 nibble")
    peff.add_argument("--dry-run", action="store_true")

    pk = sub.add_parser("key", help="paint one key via CANVAS + 5AA5 (flash write)")
    pk.add_argument("key")
    pk.add_argument("color")
    pk.add_argument("--dry-run", action="store_true")

    pp = sub.add_parser("paint", help="paint many keys: paint W=ff0000 A=00ff00 (flash write)")
    pp.add_argument("specs", nargs="+")
    pp.add_argument("--dry-run", action="store_true")

    pa = sub.add_parser("animate", help="host-side per-key animation (volatile stream)")
    pa.add_argument("name")
    pa.add_argument("--color", default="ff0000", help="base color (name or hex)")
    pa.add_argument("--speed", type=float, default=1.0, help="animation speed multiplier")
    pa.add_argument("--fps", type=int, default=30, help="frames per second")
    pa.add_argument("--duration", type=float, help="stop after N seconds (default: until Ctrl+C)")

    prs = sub.add_parser("restore", help="build+sends full Restore sequence from Cfg.ini (flash write)")
    prs.add_argument("cfg")
    prs.add_argument("--dry-run", action="store_true")
    prs.add_argument("--delay-ms", type=int, default=30)

    psudev = sub.add_parser("setup-udev", help="install 99-k617.rules + reload udev (needs root)")
    psudev.add_argument("--dry-run", action="store_true")

    return p


def main(dev: bool = False) -> int:
    p = _build_parser(dev)
    args = p.parse_args()

    fn = {
        "list": cmd_list, "cfg": cmd_cfg, "inspect": cmd_inspect,
        "diff": cmd_diff, "export": cmd_export, "replay": cmd_replay,
        "rgb": cmd_rgb, "effect": cmd_effect, "key": cmd_key,
        "paint": cmd_paint, "animate": cmd_animate, "restore": cmd_restore,
        "setup-udev": cmd_setup_udev,
    }[args.cmd]
    return fn(args)


def main_dev() -> int:
    """Dev entry point: full toolkit including capture/RE tools."""
    return main(dev=True)


def cmd_setup_udev(args) -> int:
    from .udev_rules import install_udev_rules

    return install_udev_rules(dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main_dev())