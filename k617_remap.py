"""k617-ctrl — Linux tooling for the Redragon K617 Fizz.

Reverse-engineering workspace + reference controllers for the keyboard's
vendor interface. Everything is built on the previously reverse-engineered
feature-report protocol (see docs/RE_GUIDE.md).

Usage:
    k617_remap.py list
    k617_remap.py cfg <Cfg.ini>
    k617_remap.py inspect <cap.json>
    k617_remap.py diff <capA.json> <capB.json>
    k617_remap.py export <cap.json> <frames.json>
    k617_remap.py replay <frames.json> [--dry-run] [--delay-ms N]
    k617_remap.py rgb <color>              # known-good RGB writer (FLASH WRITE!)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from k617_capture import diff_captures, export_frames, load_frames, load_tshark_json, significant
from k617_cfg import CfgIni
from k617_hid import K617, NoDeviceError, rgb_sequence
from k617_keymap import KeymapEncoder


def cmd_list(_):
    import hid
    from k617_protocol import PID, VID

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
        from k617_protocol import frame_kind
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


def cmd_rgb(args):
    from k617_protocol import LED_INDEX

    color = {"red": (255, 0, 0), "green": (0, 255, 0), "blue": (0, 0, 255),
             "white": (255, 255, 255), "off": (0, 0, 0)}.get(args.color.lower())
    if color is None:
        h = args.color.lstrip("#")
        color = (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)) if len(h) == 6 else None
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
        dev.send_sequence(rgb_all(color))
    finally:
        dev.close()
    return 0


def cmd_restore(args):
    cfg = CfgIni(args.cfg)
    keymap = KeymapEncoder(cfg).build()
    const = Path(__file__).resolve().parent / "data"
    frames = [
        bytes.fromhex("050581000000"),       # INIT
        bytes.fromhex("0583b6000000"),       # INIT
        (const / "const-mode.bin").read_bytes(),
        (const / "const-canvas.bin").read_bytes(),
        (const / "const-routing.bin").read_bytes(),
        keymap,                               # 06 04 d4 keymap block
        (const / "const-exec.bin").read_bytes(),
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
    prs = sub.add_parser("restore", help="build+sends full Restore sequence from Cfg.ini (flash write)")
    prs.add_argument("cfg")
    prs.add_argument("--dry-run", action="store_true")
    prs.add_argument("--delay-ms", type=int, default=30)

    args = p.parse_args()
    fn = {
        "list": cmd_list, "cfg": cmd_cfg, "inspect": cmd_inspect,
        "diff": cmd_diff, "export": cmd_export, "replay": cmd_replay,
        "rgb": cmd_rgb, "restore": cmd_restore,
    }[args.cmd]
    return fn(args)


if __name__ == "__main__":
    sys.exit(main())