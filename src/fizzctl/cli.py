"""fizzctl — command-line interface.

User commands (``fizzctl``):
    fizzctl rgb <color> [--brightness N]  # whole-board solid color (flash write)
    fizzctl effect <name>            # firmware-native effect (FLASH WRITE)
    fizzctl key <key> <color>        # paint one key (FLASH WRITE)
    fizzctl paint <key>=<color>...   # paint many keys (FLASH WRITE)
    fizzctl animate <name>           # host-side animation (volatile stream)
    fizzctl keymap <Cfg.ini>           # write full keymap from Cfg.ini (FLASH WRITE)
    fizzctl restore                  # restore factory keymap+lighting (FLASH WRITE)
    fizzctl macro --key K <text>     # bind a macro that types text (FLASH WRITE)
    fizzctl macro --read             # dump on-device macros + their key bindings
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
from pathlib import Path

from .animations import cmd_animate
from .capture import diff_captures, export_frames, load_frames, load_tshark_json, significant
from .cfg import CfgIni
from .hid import NoDeviceError, open_device, read_keymap, read_lighting, read_macro, send_burst
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


def _live_lighting(dev, debug: bool = False) -> list[bytes]:
    """Read the current MODE/CANVAS/ROUTING/EXEC so a keymap/macro write keeps
    the user's effect, color and brightness instead of resetting them."""
    try:
        frames = read_lighting(dev)
        if debug:
            print("  read back current MODE/CANVAS/ROUTING/EXEC (lighting kept)")
        return frames
    except Exception as e:                     # keep working on read failure
        if debug:
            print(f"  could not read current lighting ({e}); using stock frames")
        return [bytes(f) for f in RESTORE_CONSTANT_FRAMES]


def _live_keymap(dev, debug: bool = False) -> bytes:
    """Read the device's current base keymap so a macro write keeps the user's
    remaps; fall back to the baked keymap if the read fails."""
    from .blobs import CONST_KEYMAP
    try:
        keymap = read_keymap(dev)
        if debug:
            print("  read back current keymap (your remaps are kept)")
        return keymap
    except Exception as e:
        if debug:
            print(f"  could not read current keymap ({e}); using baked keymap")
        return bytes(CONST_KEYMAP)


def _live_macro(dev, debug: bool = False) -> bytes:
    """Read the device's current macro table so a write keeps every slot;
    fall back to an empty table if the read fails."""
    from .macro import build_macro_frame
    try:
        return read_macro(dev)
    except Exception as e:                   # keep working on read failure
        if debug:
            print(f"  could not read macro table ({e}); using empty table")
        return build_macro_frame({})


def _binding_for_hid(keymap, hid: int) -> tuple[int, int] | None:
    """``(offset, slot)`` of the macro binding whose stock record outputs
    ``hid`` — i.e. which bound physical key this is."""
    from .blobs import CONST_KEYMAP
    from .macro import collect_bindings
    for off, _mode, slot in collect_bindings(keymap):
        rec = CONST_KEYMAP[off:off + 4]
        if rec[0] in (0x00, 0x06) and rec[3] == (hid & 0xFF):
            return off, slot
    return None


def cmd_macro_remove_all(args):
    """Remove every macro and its key binding (flash write).

    The current keymap (including your remaps) and lighting are kept; only
    the macro slots and bindings are cleared.

    Examples:
        fizzctl macro --remove-all
    """
    from .blobs import CONST_KEYMAP
    from .macro import build_macro_frame, collect_bindings, slots_in_frame

    try:
        dev = open_device(debug=args.debug)
    except NoDeviceError:
        return 1
    if dev is None:
        return 1
    try:
        mode_f, canvas_f, routing_f, exec_f = _live_lighting(dev, args.debug)
        base = bytearray(_live_keymap(dev, args.debug))
        live_mf = _live_macro(dev, args.debug)
        bindings = collect_bindings(base)
        if not bindings and not slots_in_frame(live_mf):
            print("no macros to remove")
            return 0
        for off, _mode, _slot in bindings:
            base[off:off + 4] = bytes(CONST_KEYMAP[off:off + 4])
        frames = [
            bytes.fromhex("0583b6000000"),   # INIT
            mode_f, canvas_f, routing_f,     # current lighting (kept)
            build_macro_frame({}),           # wipe all macro slots
            bytes(base),                     # keymap with bindings restored
            exec_f,                          # EXEC (5AA5 commit)
        ]
        send_burst(dev, frames, handshake=False, delay_ms=args.burst_ms)
    finally:
        dev.close()
    print(f"removed {len(bindings)} macro binding(s) and cleared all macro slots")
    return 0


def cmd_keymap(args):
    """Write a full keymap from a Cfg.ini (flash write).

    Existing macro bindings and slots are read back from the device and
    re-applied on top, and the current lighting (effect, color, brightness)
    is kept.

    Examples:
        fizzctl keymap cfgs/cfg_final.ini
    """
    from .blobs import CONST_KEYMAP
    from .macro import collect_bindings, relocate_bindings, slots_in_frame

    keymap = bytearray(KeymapEncoder(CfgIni(args.cfg)).build())
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
        mode, canvas, routing, exec_ = _live_lighting(dev, args.debug)
        live_km = _live_keymap(dev, args.debug)
        live_mf = _live_macro(dev, args.debug)
        keymap, warnings = relocate_bindings(live_km, keymap, CONST_KEYMAP)
        for w in warnings:
            print(f"warning: {w}")
        nb = len(collect_bindings(live_km))
        if args.debug:
            print(f"built keymap from {args.cfg} (re-applied {nb} macro binding(s))")
        # exact capture order, but with the device's live lighting blocks
        frames = [
            bytes.fromhex("050581000000"),       # INIT
            bytes.fromhex("0583b6000000"),       # INIT
            mode, canvas, routing,                # current lighting
        ]
        if slots_in_frame(live_mf):
            frames.append(live_mf)                # 06 05 dc (slots kept)
        frames += [
            bytes(keymap),                        # 06 04 d4 keymap block
            exec_,                                # EXEC (5AA5 commit)
        ]
        send_burst(dev, frames, handshake=False, delay_ms=args.delay_ms)
    finally:
        dev.close()
    print(f"wrote keymap from {args.cfg} to flash")
    return 0


def _stock_cfg() -> str:
    """Path to the packaged stock.ini (factory keymap fixture)."""
    return str(Path(__file__).with_name("stock.ini"))


def cmd_restore(args):
    """Restore the factory keymap, lighting and macro state (flash write).

    Writes the packaged ``stock.ini`` and the stock lighting frames, and wipes
    all macro slots.  Unlike ``keymap``/``macro`` it does *not* keep your
    current effect/color or your macros.

    Examples:
        fizzctl restore
    """
    from .macro import build_macro_frame

    keymap = bytearray(KeymapEncoder(CfgIni(_stock_cfg())).build())
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
        mode, canvas, routing, exec_ = (bytes(f) for f in RESTORE_CONSTANT_FRAMES)
        frames = [
            bytes.fromhex("050581000000"),   # INIT
            bytes.fromhex("0583b6000000"),   # INIT
            mode, canvas, routing,           # factory lighting
            build_macro_frame({}),           # wipe all macro slots
            bytes(keymap),                   # factory keymap
            exec_,                           # EXEC (5AA5 commit)
        ]
        send_burst(dev, frames, handshake=False, delay_ms=args.delay_ms)
    finally:
        dev.close()
    print("restored factory keymap, lighting and macros")
    return 0


def cmd_macro_read(args):
    """Read the on-device macro table back (dev tool).

    Shows every non-empty slot with the key(s) bound to it (a slot can be
    shared) and the events it types.  Since the keymap is read alongside the
    macro table, bindings are identified by the stock record at their live
    offset (the CONST_KEYMAP oracle), and keys bound with ``--until-released``
    are flagged as such.

    Examples:
        fizzctl macro --read
    """
    from .blobs import CONST_KEYMAP
    from .macro import MAX_SLOTS, MODE_UNTIL_RELEASED, NAME_TO_HID, \
        SLOT_BASE, SLOT_STRIDE, collect_bindings, decode_slot

    names = {hid: name for name, hid in NAME_TO_HID.items()}
    try:
        dev = open_device(debug=args.debug)
    except NoDeviceError:
        return 1
    if dev is None:
        return 1
    try:
        frame = read_macro(dev)
        keymap = read_keymap(dev)
    finally:
        dev.close()

    binds: dict[int, list[tuple[str, int]]] = {}
    for off, mode, slot in collect_bindings(keymap):
        rec = CONST_KEYMAP[off:off + 4]
        hid = rec[3] if rec[0] in (0x00, 0x06) else None
        label = (names.get(hid) if hid else None) or (f"key 0x{hid:02x}" if hid else f"@+{off:#06x}")
        if mode & MODE_UNTIL_RELEASED:
            label += " (until released)"
        binds.setdefault(slot, []).append(label)

    print(f"macro table header: {frame[:5].hex(' ')}")
    found = 0
    for i in range(MAX_SLOTS):
        base = SLOT_BASE + i * SLOT_STRIDE
        cycles, events = decode_slot(frame[base:base + SLOT_STRIDE])
        if not events and cycles == 0:
            continue
        found += 1
        line = f"slot{i}:"
        if cycles:
            line += f" cycles={cycles}"
        if i in binds:
            line += "  bound to: " + ", ".join(binds[i])
        print(line)
        ev = " ".join(f"{d}{names.get(h, f'#{h:02x}')}{'R' if r else 'P'}"
                      for d, h, r in events)
        print(f"  {ev}")
    if not found:
        print("  (no non-empty slots read back)")
    print(f"({found} slot(s) non-empty)")
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
    """Bind a key to a macro that types text (flash write).

    The device's current keymap and macro table are read back first, so
    existing macros, bindings and remaps are kept with no local state file;
    the current lighting is kept too.  ``--remove-all`` unbinds every macro
    instead.

    Examples:
        fizzctl macro --key CapsLock rgb
        fizzctl macro --key LAlt --delay-ms 50 --cycles 3 hello
        fizzctl macro --until-released --key 2 aaaa
        fizzctl macro --read            # dump the on-device macro table + bindings
        fizzctl macro --remove-all
    """
    from .macro import (
        MODE_CYCLES, MODE_UNTIL_RELEASED, MAX_SLOTS, NAME_TO_HID,
        build_macro_frame, encode_slot, locate_key, slots_in_frame,
        text_events,
    )

    if args.read:
        return cmd_macro_read(args)
    if args.remove_all:
        return cmd_macro_remove_all(args)
    if not args.key:
        print("error: --key is required")
        return 1
    if args.text is None:
        print("error: macro text is required")
        return 1
    if args.key not in NAME_TO_HID:
        print(f"unknown key {args.key!r}")
        return 1
    if args.cycles < 1:
        print("--cycles must be at least 1")
        return 1

    try:
        events = text_events(args.text, args.delay_ms)
    except KeyError as e:
        print(f"cannot type character {e.args[0]!r}")
        return 1

    mode = MODE_UNTIL_RELEASED if args.until_released else MODE_CYCLES
    try:
        dev = open_device(debug=args.debug)
    except NoDeviceError:
        return 1
    if dev is None:
        return 1
    try:
        mode_f, canvas_f, routing_f, exec_f = _live_lighting(dev, args.debug)
        base = bytearray(_live_keymap(dev, args.debug))
        live_mf = _live_macro(dev, args.debug)
        slots = slots_in_frame(live_mf)
        hid = NAME_TO_HID[args.key]

        off = locate_key(base, hid)
        if off is None:
            # already bound: the device echoes it as a `10` record, so find
            # which binding this key is by its stock record instead
            pos = _binding_for_hid(base, hid)
            if pos is None:
                print(f"could not find key {args.key!r} in the keymap")
                return 1
            bind_off, slot_idx = pos
        else:
            free = [i for i in range(MAX_SLOTS) if i not in slots]
            if not free:
                print(f"all {MAX_SLOTS} macro slots are in use")
                return 1
            bind_off, slot_idx = off, free[0]

        slots[slot_idx] = encode_slot(args.cycles, events)
        base[bind_off:bind_off + 4] = bytes((0x10, 0x00, mode, slot_idx))

        if args.debug:
            print(f"macro: slot{slot_idx} cycles={args.cycles} events={len(events)} "
                  f"mode={mode:#04x} bind={args.key}")

        # exact capture order, but with the device's live blocks
        frames = [
            bytes.fromhex("0583b6000000"),   # INIT
            mode_f, canvas_f, routing_f,     # current lighting
            build_macro_frame(slots),        # 06 05 dc (all slots)
            bytes(base),                     # 06 04 d4 (all bindings)
            exec_f,                          # EXEC (5AA5 commit)
        ]
        send_burst(dev, frames, handshake=False, delay_ms=args.burst_ms)
    finally:
        dev.close()
    print(f"bound {args.key} -> macro typing {args.text!r} (slot {slot_idx})")
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
  fizzctl animate chase --daemon    # background daemon; `animate stop` ends it
  fizzctl animate stop
  fizzctl animate                                 # lists all animations
""".rstrip(),
                        formatter_class=argparse.RawDescriptionHelpFormatter)
    pa.add_argument("name", nargs="?",
                    help="animation name (omit or run `animate` alone to list all)")
    pa.add_argument("-c", "--color", default="ff0000", help="base color (name or hex)")
    pa.add_argument("-s", "--speed", type=float, default=1.0, help="animation speed multiplier")
    pa.add_argument("-f", "--fps", type=int, default=30, help="frames per second")
    pa.add_argument("--duration", type=float, help="stop after N seconds (default: until Ctrl+C)")
    pa.add_argument("--daemon", action="store_true",
                    help="run in a background daemon and free the terminal")

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
                        help="bind a key that types TEXT, --remove-all, or --read (flash write)",
                        description="""
Examples:
  fizzctl macro --key CapsLock rgb
  fizzctl macro --key LAlt --delay-ms 50 --cycles 3 hello
  fizzctl macro --until-released --key 2 aaaa
  fizzctl macro --read            # dump on-device macros + their key bindings
  fizzctl macro --remove-all
""".rstrip(),
                        formatter_class=argparse.RawDescriptionHelpFormatter)
    pm.add_argument("text", nargs="?", help="characters the macro types")
    pm.add_argument("-k", "--key",
                    help="key to bind (e.g. CapsLock, LAlt, A)")
    pm.add_argument("--read", action="store_true",
                    help="read the on-device macro table back, showing each slot's key binding")
    pm.add_argument("--remove-all", action="store_true",
                    help="unbind every macro and wipe all macro slots")
    pm.add_argument("--delay-ms", type=int, default=30,
                    help="delay between macro events (default 30)")
    pm.add_argument("--cycles", type=int, default=1,
                    help="play the macro N times (default 1)")
    pm.add_argument("--until-released", action="store_true",
                    help="cycle until the bound key is released")
    pm.add_argument("--burst-ms", type=int, default=30,
                    help="delay between USB frames (default 30)")

    pres = sub.add_parser("restore",
                        help="restore the factory keymap, lighting and macros (flash write)",
                        description="""
Writes the packaged stock.ini and the stock lighting frames, and wipes all
macro slots. Unlike keymap/macro it does not keep your current effect/color
or your macros.

Examples:
  fizzctl restore
""".rstrip(),
                        formatter_class=argparse.RawDescriptionHelpFormatter)
    pres.add_argument("--delay-ms", type=int, default=30)

    prd = sub.add_parser("setup-udev", help="install 99-k617.rules + reload udev (needs root)")

    return p


def main(dev: bool = False) -> int:
    p = _build_parser(dev)
    args = p.parse_args()

    fn = {
        "list": cmd_list, "cfg": cmd_cfg, "inspect": cmd_inspect,
        "diff": cmd_diff, "export": cmd_export, "replay": cmd_replay,
        "rgb": cmd_rgb, "effect": cmd_effect, "key": cmd_key,
        "paint": cmd_paint, "animate": cmd_animate, "keymap": cmd_keymap,
        "restore": cmd_restore, "macro": cmd_macro,
        "setup-udev": cmd_setup_udev,
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