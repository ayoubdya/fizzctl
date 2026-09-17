"""fizzctl — command-line interface.

User commands (``fizzctl``):
    fizzctl rgb <color> [--brightness N]  # whole-board solid color (flash write)
    fizzctl effect <name>            # firmware-native effect (FLASH WRITE)
    fizzctl key <key> <color>        # paint one key (FLASH WRITE)
    fizzctl paint <key>=<color>...   # paint many keys (FLASH WRITE)
    fizzctl animate <name>           # host-side animation (volatile stream)
    fizzctl keymap <Cfg.ini>           # write full keymap from Cfg.ini (FLASH WRITE)
    fizzctl macro --key K <text>     # bind a macro that types text (FLASH WRITE)
    fizzctl setup-udev               # install 99-k617.rules (needs root)

Dev commands (``fizzctl-dev``, reverse-engineering toolkit):
    fizzctl-dev list
    fizzctl-dev cfg <Cfg.ini>
    fizzctl-dev inspect <cap.json>
    fizzctl-dev diff <capA.json> <capB.json>
    fizzctl-dev export <cap.json> <frames.json>
    fizzctl-dev replay <frames.json> [--delay-ms N]
    plus every user command above
"""
from __future__ import annotations

import argparse
import sys

from .animations import cmd_animate
from .capture import diff_captures, export_frames, load_frames, load_tshark_json, significant
from .cfg import CfgIni
from .hid import NoDeviceError, open_device, send_burst
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
        dev = open_device(debug=args.debug)
    except NoDeviceError:
        return 1
    if dev is None:
        return 1
    try:
        send_burst(dev, frames, handshake=False, delay_ms=args.delay_ms)
    finally:
        dev.close()
    return 0


def cmd_keymap(args):
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
    try:
        dev = open_device(debug=args.debug)
    except NoDeviceError:
        return 1
    if dev is None:
        return 1
    try:
        send_burst(dev, frames, handshake=False, delay_ms=args.delay_ms)
    finally:
        dev.close()
    print(f"wrote keymap from {args.cfg} to flash")
    return 0


def cmd_rgb(args):
    """Shortcut for `effect fixed-on <color>` (whole-board solid color).

    Examples:
        fizzctl rgb ff0000
        fizzctl rgb 00ff00 --brightness 4
    """
    args.name = "fixed-on"
    args.speed = None
    return cmd_effect(args)


def cmd_effect(args):
    """Run a firmware-native effect (flash write).

    Examples:
        fizzctl effect rainbow                 # default speed/brightness
        fizzctl effect rainbow --speed 2 --brightness 4
        fizzctl effect waterfall --color ff8800
        fizzctl effect static --brightness 1
    """
    from .effects import (
        EFFECT_ACCEPTS_COLOR, EFFECT_DEFAULTS, EFFECT_ID,
        _ALIASES, encode_firmware_effect, parse_color,
    )

    name = _ALIASES.get(args.name, args.name)
    if name is None or name not in EFFECT_ID:
        if name is not None:
            print(f"unknown effect {name!r}")
        print("Firmware effects (22). Run like:  fizzctl effect rainbow --speed 2 --brightness 4")
        for n, eid in EFFECT_ID.items():
            spd, bri = EFFECT_DEFAULTS[n]
            spd = spd + 1 if spd else 0   # stored 0-based, displayed 1..5
            color = "yes" if n in EFFECT_ACCEPTS_COLOR else "-"
            print(f"  {n:18s} id={eid:#04x} color:{color:3s} default speed={spd} brightness={bri}")
        return 0 if name is None else 1

    color = None
    if args.color:
        color = parse_color(args.color)
        if color is None:
            print(f"bad color {args.color!r}: use a name or hex")
            return 1

    frames = encode_firmware_effect(name, color, speed=args.speed, brightness=args.brightness)
    try:
        dev = open_device(debug=args.debug)
    except NoDeviceError:
        return 1
    if dev is None:
        return 1
    try:
        send_burst(dev, frames)
    finally:
        dev.close()
    desc = f"effect {name}"
    if color:
        desc += f" (color=#{color[0]:02x}{color[1]:02x}{color[2]:02x})"
    if args.speed is not None or args.brightness is not None:
        desc += f" (speed={args.speed}, brightness={args.brightness})"
    print(f"applied {desc}")
    return 0


def cmd_macro(args):
    """Bind a macro that types ``text`` to a key (flash write).

    Examples:
        fizzctl macro --key CapsLock rgb
        fizzctl macro --key LAlt --delay-ms 50 --cycles 3 hello
        fizzctl macro --cfg cfgs/cfg_final.ini --key A --until-released abc
    """
    from .blobs import CONST_KEYMAP
    from .macro import (
        MODE_CYCLES, MODE_UNTIL_RELEASED, NAME_TO_HID,
        bind_macro, encode_macro_frame, text_events,
    )

    if args.key not in NAME_TO_HID:
        print(f"unknown key {args.key!r}")
        return 1
    hid = NAME_TO_HID[args.key]

    if args.cfg:
        base = bytearray(KeymapEncoder(CfgIni(args.cfg)).build())
    else:
        base = bytearray(CONST_KEYMAP)

    try:
        events = text_events(args.text, args.delay_ms)
    except KeyError as e:
        print(f"cannot type character {e.args[0]!r}")
        return 1

    mode = MODE_UNTIL_RELEASED if args.until_released else MODE_CYCLES
    macro_frame = encode_macro_frame([(args.cycles, events)])

    off = bind_macro(base, hid, 0, mode)
    if off is None:
        print(f"could not find key {args.key!r} in the keymap")
        return 1

    # exact capture order: INIT, MODE, CANVAS, ROUTING, MACRO, KEYMAP, EXEC
    frames = [
        bytes.fromhex("0583b6000000"),   # INIT
        RESTORE_CONSTANT_FRAMES[0],      # MODE
        RESTORE_CONSTANT_FRAMES[1],      # CANVAS
        RESTORE_CONSTANT_FRAMES[2],      # ROUTING
        macro_frame,                     # 06 05 dc
        bytes(base),                     # 06 04 d4 (with binding)
        RESTORE_CONSTANT_FRAMES[3],      # EXEC (5AA5 commit)
    ]
    if args.debug:
        print(f"macro: slot0 cycles={args.cycles} events={len(events)} "
              f"mode={mode:#04x} bind={args.key}@{off:#05x}")

    try:
        dev = open_device(debug=args.debug)
    except NoDeviceError:
        return 1
    if dev is None:
        return 1
    try:
        send_burst(dev, frames, handshake=False, delay_ms=args.burst_ms)
    finally:
        dev.close()
    print(f"bound {args.key} -> macro typing {args.text!r}")
    return 0


def cmd_key(args):
    """Paint a single key (`key W red` == `paint W=red`)."""
    args.specs = [f"{args.key}={args.color}"]
    return cmd_paint(args)


def cmd_paint(args):
    """Paint many keys via the CANVAS + 5AA5 execute path (flash write):
    fizzctl paint W=ff0000 A=00ff00 S=0000ff D=ffffff
    """
    from .effects import parse_color
    from .hid import rgb_sequence
    from .protocol import NAME_TO_INDEX

    colors = {}
    for spec in args.specs:
        if "=" not in spec:
            print(f"bad spec {spec!r}: expected KEY=COLOR")
            return 1
        key, c = spec.rsplit("=", 1)
        if key not in NAME_TO_INDEX:
            print(f"unknown key {key!r}")
            return 1
        color = parse_color(c)
        if color is None:
            print(f"bad color {c!r}")
            return 1
        colors[key] = color
    frames = rgb_sequence(colors)
    try:
        dev = open_device(debug=args.debug)
    except NoDeviceError:
        return 1
    if dev is None:
        return 1
    try:
        send_burst(dev, frames)
        print(f"painted {len(colors)} key{'s' if len(colors) != 1 else ''}")
    finally:
        dev.close()
    return 0


def _build_parser(dev: bool) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="fizzctl" if not dev else "fizzctl-dev",
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
        pr.add_argument("--delay-ms", type=int, default=30)

    px = sub.add_parser("rgb",
                      help="solid color: rgb red -b 4 (shortcut for `effect fixed-on`)",
                      description="""
Examples:
  fizzctl rgb red
  fizzctl rgb ff0000 --brightness 4
  fizzctl rgb green -b 3
""".rstrip(),
                      formatter_class=argparse.RawDescriptionHelpFormatter)
    px.add_argument("color")
    px.add_argument("-b", "--brightness", type=int, help="0..4 (level; higher = brighter)")

    peff = sub.add_parser("effect",
                          help="run a firmware-native effect (flash write); run without a name to list all",
                          description="""
Examples:
  fizzctl effect rainbow                      # defaults from stock config
  fizzctl effect rainbow --speed 2 --brightness 4
  fizzctl effect waterfall --color cyan
  fizzctl effect                              # lists all 22 effects
""".rstrip(),
                          formatter_class=argparse.RawDescriptionHelpFormatter)
    peff.add_argument("name", nargs="?",
                      help="effect name (omit or run `effect` alone to list all)")
    peff.add_argument("-c", "--color", help="base color (name or hex) — RGB/single-color mode")
    peff.add_argument("-s", "--speed", type=int, help="1..5 (level; higher = faster)")
    peff.add_argument("-b", "--brightness", type=int, help="0..4 (level; higher = brighter)")

    pk = sub.add_parser("key",
                        help="paint one key: key W red (flash write)",
                        description="""
Examples:
  fizzctl key W ff0000
  fizzctl key A yellow
""".rstrip(),
                        formatter_class=argparse.RawDescriptionHelpFormatter)
    pk.add_argument("key")
    pk.add_argument("color")

    pp = sub.add_parser("paint",
                        help="paint many keys: paint W=ff0000 A=00ff00 (flash write)",
                        description="""
Examples:
  fizzctl paint W=ff0000 A=00ff00 S=ffff00 D=ff00ff
  fizzctl paint W=red A=orange S=yellow D=green
""".rstrip(),
                        formatter_class=argparse.RawDescriptionHelpFormatter)
    pp.add_argument("specs", nargs="+")

    pa = sub.add_parser("animate",
                        help="host-side per-key animation: animate chase -c red -s 2 (volatile stream)",
                        description="""
Examples:
  fizzctl animate rainbow --fps 30 --duration 10
  fizzctl animate chase --color yellow --speed 2
  fizzctl animate solid --color ff0000
  fizzctl animate                                 # lists all animations
""".rstrip(),
                        formatter_class=argparse.RawDescriptionHelpFormatter)
    pa.add_argument("name", nargs="?",
                    help="animation name (omit or run `animate` alone to list all)")
    pa.add_argument("-c", "--color", default="ff0000", help="base color (name or hex)")
    pa.add_argument("-s", "--speed", type=float, default=1.0, help="animation speed multiplier")
    pa.add_argument("-f", "--fps", type=int, default=30, help="frames per second")
    pa.add_argument("--duration", type=float, help="stop after N seconds (default: until Ctrl+C)")

    prs = sub.add_parser("keymap",
                        help="write the keymap from a Cfg.ini: keymap Cfg.ini (flash write)",
                        description="""
Examples:
  fizzctl keymap captures/cfg-runs/cfg_r2_stock.ini
""".rstrip(),
                        formatter_class=argparse.RawDescriptionHelpFormatter)
    prs.add_argument("cfg")
    prs.add_argument("--delay-ms", type=int, default=30)

    pm = sub.add_parser("macro",
                        help="bind a macro that types TEXT to a key (flash write)",
                        description="""
Examples:
  fizzctl macro --key CapsLock rgb
  fizzctl macro --key LAlt --delay-ms 50 --cycles 3 hello
  fizzctl macro --cfg cfgs/cfg_final.ini --key A --until-released abc
""".rstrip(),
                        formatter_class=argparse.RawDescriptionHelpFormatter)
    pm.add_argument("text", help="characters the macro types")
    pm.add_argument("-k", "--key", required=True,
                    help="key to bind (e.g. CapsLock, LAlt, A)")
    pm.add_argument("--cfg", help="rebuild the keymap from this Cfg.ini "
                                  "(default: baked stock keymap)")
    pm.add_argument("--delay-ms", type=int, default=30,
                    help="delay between macro events (default 30)")
    pm.add_argument("--cycles", type=int, default=1,
                    help="play the macro N times (default 1)")
    pm.add_argument("--until-released", action="store_true",
                    help="cycle until the bound key is released")
    pm.add_argument("--burst-ms", type=int, default=30,
                    help="delay between USB frames (default 30)")

    psudev = sub.add_parser("setup-udev", help="install 99-k617.rules + reload udev (needs root)")

    return p


def main(dev: bool = False) -> int:
    p = _build_parser(dev)
    args = p.parse_args()

    fn = {
        "list": cmd_list, "cfg": cmd_cfg, "inspect": cmd_inspect,
        "diff": cmd_diff, "export": cmd_export, "replay": cmd_replay,
        "rgb": cmd_rgb, "effect": cmd_effect, "key": cmd_key,
        "paint": cmd_paint, "animate": cmd_animate, "keymap": cmd_keymap,
        "macro": cmd_macro, "setup-udev": cmd_setup_udev,
    }[args.cmd]
    return fn(args)


def main_dev() -> int:
    """Dev entry point: full toolkit including capture/RE tools."""
    return main(dev=True)


def cmd_setup_udev(args) -> int:
    from .udev_rules import install_udev_rules

    return install_udev_rules()


if __name__ == "__main__":
    sys.exit(main_dev())