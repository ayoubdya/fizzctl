"""k617-ctrl — command-line interface.

Usage:
    k617-ctrl list
    k617-ctrl cfg <Cfg.ini>
    k617-ctrl inspect <cap.json>
    k617-ctrl diff <capA.json> <capB.json>
    k617-ctrl export <cap.json> <frames.json>
    k617-ctrl replay <frames.json> [--dry-run] [--delay-ms N]
    k617-ctrl rgb <color>              # known-good RGB writer (FLASH WRITE!)
    k617-ctrl restore <Cfg.ini>        # full keymap Restore sequence (FLASH WRITE!)
"""
from __future__ import annotations

import argparse
import sys

from .animations import cmd_animate
from .capture import diff_captures, export_frames, load_frames, load_tshark_json, significant
from .cfg import CfgIni
from .effects import encode_per_key_frame
from .hid import K617, NoDeviceError, rgb_all, send_rgb
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
        dev = K617(dry_run=args.dry_run)
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
    print(f"built {len(frames)} frames from {args.cfg}")
    print(f"  keymap block: {len(keymap)}B, must equal 1032")
    if len(keymap) != 1032:
        print("error: keymap block is not 1032 bytes")
        return 1
    if not args.dry_run:
        print("warning: commits keymap to flash (5AA5). Continue? y/N")
        if input().strip().lower() != "y":
            print("aborted")
            return 0
    try:
        dev = K617(dry_run=args.dry_run)
    except NoDeviceError as e:
        print(f"error: {e}")
        return 1
    try:
        dev.send_sequence(frames, delay_ms=args.delay_ms)
    finally:
        dev.close()
    return 0


def cmd_rgb(args):
    from .protocol import LED_INDEX

    color = parse_color(args.color)
    if color is None:
        print("bad color: use a name or hex")
        return 1
    print("warning: this COMMITS TO FLASH (5AA5). Continue? y/N")
    if input().strip().lower() != "y":
        print("aborted")
        return 0
    try:
        dev = K617()
    except NoDeviceError as e:
        print(f"error: {e}")
        return 1
    try:
        send_rgb(dev, rgb_all(color))
    finally:
        dev.close()
    return 0


def cmd_effect(args):
    """Run one of the 8 firmware-native effects (flash write).

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

    if not args.dry_run:
        print("warning: this COMMITS TO FLASH (5AA5). Continue? y/N")
        if input().strip().lower() != "y":
            print("aborted")
            return 0
    try:
        dev = K617(dry_run=args.dry_run)
    except NoDeviceError as e:
        print(f"error: {e}")
        return 1
    try:
        frames = encode_firmware_effect(name, color, speed=args.speed, brightness=args.brightness)
        if args.dry_run:
            for i, f in enumerate(frames):
                print(f"  [{i + 1}/5] {_kind(f)} ({len(f)}B)")
        else:
            send_firmware_effect(dev, frames)
    finally:
        dev.close()
    return 0


def cmd_key(args):
    """Paint a single key (per-key protocol, host-side/volatile, no flash)."""
    from .hid import send_per_key

    color = parse_color(args.color)
    if color is None:
        print(f"bad color {args.color!r}")
        return 1
    frame = encode_per_key_frame({args.key: color})
    try:
        dev = K617(dry_run=args.dry_run)
    except NoDeviceError as e:
        print(f"error: {e}")
        return 1
    try:
        if args.dry_run:
            print(f"[dry-run] would paint {args.key}={color} via 382-byte report")
        else:
            send_per_key(dev, frame)
            print(f"painted {args.key} -> {color}")
    finally:
        dev.close()
    return 0


def cmd_paint(args):
    """Paint many keys in one 382-byte per-key report:
    k617-ctrl paint W=ff0000 A=00ff00 S=0000ff D=ffffff
    """
    from .hid import send_per_key

    colors = {}
    for spec in args.specs:
        if "=" not in spec:
            print(f"bad spec {spec!r}: expected KEY=COLOR")
            return 1
        key, c = spec.split("=", 1)
        color = parse_color(c)
        if color is None:
            print(f"bad color {c!r}")
            return 1
        colors[key] = color
    try:
        frame = encode_per_key_frame(colors)
    except KeyError as e:
        print(f"error: {e}")
        return 1
    try:
        dev = K617(dry_run=args.dry_run)
    except NoDeviceError as e:
        print(f"error: {e}")
        return 1
    try:
        if args.dry_run:
            print(f"[dry-run] paint {len(colors)} keys -> {colors}")
        else:
            send_per_key(dev, frame)
            print(f"painted {len(colors)} keys: {colors}")
    finally:
        dev.close()
    return 0


def parse_color(s: str) -> tuple[int, int, int] | None:
    from .effects import parse_color as _pc

    return _pc(s)


def _kind(frame: bytes) -> str:
    from .protocol import frame_kind

    return frame_kind(frame)


def main():
    p = argparse.ArgumentParser(description="Redragon K617 reverse-engineering toolkit")
    sub = p.add_subparsers(dest="cmd", required=True)

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
    px = sub.add_parser("rgb", help="known-good per-key RGB writer (flash write)")
    px.add_argument("color")

    peff = sub.add_parser("effect", help="run a firmware-native effect (flash write)")
    peff.add_argument("name")
    peff.add_argument("--color", help="base color (name or hex) — only for color-capable effects")
    peff.add_argument("--speed", type=int, help="0..15 nibble")
    peff.add_argument("--brightness", type=int, help="0..15 nibble")
    peff.add_argument("--dry-run", action="store_true")

    pk = sub.add_parser("key", help="paint one key via the per-key report (volatile)")
    pk.add_argument("key")
    pk.add_argument("color")
    pk.add_argument("--dry-run", action="store_true")

    pp = sub.add_parser("paint", help="paint many keys: paint W=ff0000 A=00ff00 (volatile)")
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

    args = p.parse_args()
    fn = {
        "list": cmd_list, "cfg": cmd_cfg, "inspect": cmd_inspect,
        "diff": cmd_diff, "export": cmd_export, "replay": cmd_replay,
        "rgb": cmd_rgb, "effect": cmd_effect, "key": cmd_key,
        "paint": cmd_paint, "animate": cmd_animate, "restore": cmd_restore,
    }[args.cmd]
    return fn(args)


if __name__ == "__main__":
    sys.exit(main())