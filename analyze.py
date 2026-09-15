"""One-shot analysis of all capture runs.

Usage (after copying tshark JSON exports into captures/):
    python3 analyze.py [captures_dir]

Expects:
    captures/r2.json   – stock Restore baseline
    captures/r3.json   – one base-layer key changed (RShift→F12)
    captures/r4.json   – one FN-layer entry added (FN+Tab→Play)
    captures/r5.json   – two base-layer keys changed (5→F11, \\→Delete)

Prints: inspected packet table per run, then byte-level diffs against R2.
"""
from __future__ import annotations

import sys
from pathlib import Path

from k617_ctrl.capture import load_tshark_json, diff_captures, significant
from k617_ctrl.protocol import frame_kind


CAPTURES = ["r2.json", "r3.json", "r4.json", "r5.json"]
LABELS = {
    "r2": "baseline stock Restore",
    "r3": "ONE base-layer change (RShift→F12)",
    "r4": "ONE FN-layer addition (FN+Tab→Play)",
    "r5": "TWO base-layer changes (5→F11, \\→Delete)",
}


def inspect_one(path: Path) -> list:
    all_recs = load_tshark_json(str(path))
    writes = significant(all_recs)
    print(f"\n--- {path.name}: {len(all_recs)} USB records, "
          f"{len(writes)} host→device writes ---")
    for r in writes:
        print(f"  {repr(r)}")
    return all_recs


def main() -> int:
    cap_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("captures")
    missing = [f for f in CAPTURES if not (cap_dir / f).exists()]
    if missing:
        print(f"missing captures in {cap_dir}: {', '.join(missing)}")
        print("copy tshark JSON exports there first (see docs/RE_GUIDE.md §1)")
        return 1

    print("=== INSPECT ===")
    runs = {}
    for name in CAPTURES:
        runs[name] = inspect_one(cap_dir / name)

    print("\n=== DIFFS vs R2 (baseline) ===")
    for name in CAPTURES:
        if name == "r2.json":
            continue
        key = name.rsplit(".", 1)[0]
        label = LABELS.get(key, "")
        print(f"\n--- R2 vs {key.upper()} ({label}) ---")
        writes_b = significant(runs[name])
        if not writes_b:
            print("  (empty capture — nothing to diff)")
            continue
        changes = diff_captures(runs["r2.json"], runs[name])
        if not changes:
            print("  no differences — captures identical")
            continue
        print(f"  {len(changes)} frame(s) differ:")
        for c in changes:
            print(f"\n  frame {c['frame']}: {c['kind_a']} ({c['len_a']}B) → "
                  f"{c['kind_b']} ({c['len_b']}B), "
                  f"{c['diff_count']} bytes differ")
            for off, ca, cb in c["offsets"]:
                print(f"    +0x{off:04x}  {ca:>2} → {cb:>2}")
            if c["diff_count"] > len(c["offsets"]):
                print(f"    ... {c['diff_count'] - len(c['offsets'])} more")
    return 0


if __name__ == "__main__":
    sys.exit(main())