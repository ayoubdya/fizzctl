"""Diff two sinowealth-kb-tool Intel-Hex dumps byte-wise.

Usage:
    python3 flash_diff.py before.hex after.hex [--context 8]

Reports regions (contiguous runs of changed bytes) plus their flash offsets —
the differential-flash cross-validation described in docs/RE_GUIDE.md §4.
"""
from __future__ import annotations

import argparse
import sys


def parse_ihex(path: str) -> dict[int, int]:
    """Parse an Intel HEX file into {address: byte}."""
    image: dict[int, int] = {}
    base = 0
    with open(path) as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if not line.startswith(":"):
                raise ValueError(f"{path}:{lineno}: not an IHEX record: {line!r}")
            rec = bytes.fromhex(line[1:])
            count = rec[0]
            addr = int.from_bytes(rec[1:3], "big")
            rtype = rec[3]
            data = rec[4:4 + count]
            if len(data) != count:
                raise ValueError(f"{path}:{lineno}: bad length")
            if rtype == 0x00:
                for i, byte in enumerate(data):
                    image[base + addr + i] = byte
            elif rtype == 0x01:  # EOF
                break
            elif rtype == 0x02:  # extended segment address
                base = int.from_bytes(data[:2], "big") * 16
            elif rtype == 0x04:  # extended linear address
                base = int.from_bytes(data[:2], "big") * 65536
            else:
                pass
    return image


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("before")
    ap.add_argument("after")
    ap.add_argument("--context", type=int, default=8)
    args = ap.parse_args()

    before = parse_ihex(args.before)
    after = parse_ihex(args.after)

    addrs = sorted(set(before) | set(after))
    changed: list[tuple[int, int | None, int | None]] = []
    for a in addrs:
        b, c = before.get(a), after.get(a)
        if b != c:
            changed.append((a, b, c))

    if not changed:
        print("identical images")
        return 0

    print(f"{len(changed)} changed bytes\n")
    runs: list[list[tuple[int, int | None, int | None]]] = []
    for row in changed:
        if runs and row[0] == runs[-1][-1][0] + 1:
            runs[-1].append(row)
        else:
            runs.append([row])

    for run in runs:
        lo, hi = run[0][0], run[-1][0]
        print(f"region 0x{lo:05x}..0x{hi:05x}  ({len(run)} bytes)")
        for addr, b, c in run[:args.context]:
            def fmt(v):
                return "??" if v is None else f"{v:02x}"
            print(f"  +0x{addr:04x}  {fmt(b)} -> {fmt(c)}")
        if len(run) > args.context:
            print(f"  ... {len(run) - args.context} more changed bytes")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())