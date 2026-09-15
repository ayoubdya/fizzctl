"""Cfg.ini parser for the Redragon K617 vendor software.

Understands the [OPT], [FN] and [KEY] sections used by the OEM app.  Only the
mapping triples matter for RE work:

  [FN]  K15=0x04,0x22,0x00          -> 3-byte behavior on the FN layer
  [KEY] K15=13,51,54,79, 0x02,0x9,0x00,2,42
         ^---GUI rect---^ ^-behavior-^ ^matrix^        -> base-layer key

Behavior triple first byte:
  0x02  normal key   (byte 2 = Windows Virtual-Key code)
  0x04  media key    (byte 2 = consumer code: 0x22 play/pause, 0x27 vol-, 0x26 vol+, ...)
  0x09  keyboard fn  (bytes 2-3 = internal command, e.g. 0x0e000001 lock Win)
"""
from __future__ import annotations

import configparser
import re

KEY_LINE = re.compile(
    r"^K(?P<idx>\d+)\s*=\s*(?P<geom>[^,]+,[^,]+,[^,]+,[^,]+),\s*"
    r"(?P<hex>(?:0x[0-9a-fA-F]+)(?:\s*,\s*0x[0-9a-fA-F]+)+)\s*"
    r"(?:,\s*(?P<rest>[0-9,]+))?\s*$"
)
FN_LINE = re.compile(r"^K(?P<idx>\d+)\s*=\s*(?P<hex>(?:0x[0-9a-fA-F]+)(\s*,\s*0x[0-9a-fA-F]+)+)\s*$")


def _parse_hex(s: str) -> tuple[int, ...]:
    return tuple(int(h, 16) for h in re.findall(r"0x([0-9a-fA-F]+)", s))


class KeyEntry:
    __slots__ = ("index", "geom", "behavior", "matrix")

    def __init__(self, index: int | None, geom=None, behavior=None, matrix=None):
        self.index = index
        self.geom = geom          # (x, y, x2, y2) GUI rect
        self.behavior = behavior  # (type, code, param)
        self.matrix = matrix      # (col, row) hardware address

    def __repr__(self):
        return (f"KeyEntry(K{self.index} geom={self.geom} "
                f"behavior={[f'0x{b:02X}' for b in self.behavior]} matrix={self.matrix})")


class CfgIni:
    def __init__(self, path):
        parser = configparser.RawConfigParser(strict=False, comment_prefixes=(";", "#", "//"))
        parser.optionxform = str
        with open(path, "r", errors="replace") as fh:
            parser.read_file(fh)
        self.path = path
        self.opt = dict(parser.items("OPT")) if parser.has_section("OPT") else {}
        self.fn: dict[int, tuple[int, ...]] = {}
        self.keys: dict[int, KeyEntry] = {}
        for section in parser.sections():
            for opt, raw in parser.items(section):
                line = f"{opt}={raw.strip()}"
                m = FN_LINE.match(line)
                if m and section == "FN":
                    self.fn[int(m.group("idx"))] = _parse_hex(m.group("hex"))
                    continue
                m = KEY_LINE.match(line)
                if m and section == "KEY":
                    geom = tuple(int(v) for v in m.group("geom").split(","))
                    be = _parse_hex(m.group("hex"))
                    matrix = None
                    if m.group("rest"):
                        vals = [int(v) for v in m.group("rest").split(",")]
                        matrix = tuple(vals[:2])
                    self.keys[int(m.group("idx"))] = KeyEntry(
                        int(m.group("idx")), geom, tuple(be), matrix
                    )
        # recover K<idx> from raw section lines that use K<idx> as the key
        self._fill_from_raw(parser)

    def _fill_from_raw(self, parser):
        raw = {
            s: dict(parser.items(s)) for s in parser.sections()
            if s in ("FN", "KEY")
        }
        for section in ("FN", "KEY"):
            for name, value in raw.get(section, {}).items():
                if section == "FN" and name.startswith("K") and name.isidentifier():
                    idx = int(name[1:])
                    if idx not in self.fn:
                        self.fn[idx] = _parse_hex(value)
                elif section == "KEY" and name.startswith("K"):
                    idx = int(name[1:])
                    if idx not in self.keys:
                        m = KEY_LINE.match(value.strip())
                        if m:
                            self.keys[idx] = KeyEntry(
                                idx,
                                tuple(int(v) for v in m.group("geom").split(",")),
                                _parse_hex(m.group("hex")),
                                tuple(int(v) for v in m.group("rest").split(",")[:2])
                                if m.group("rest") else None,
                            )

    @property
    def fn_entries(self) -> list[tuple[int, tuple[int, ...]]]:
        return sorted(self.fn.items())

    @property
    def key_entries(self) -> list[tuple[int, KeyEntry]]:
        return sorted(self.keys.items())


if __name__ == "__main__":
    import sys
    cfg = CfgIni(sys.argv[1] if len(sys.argv) > 1 else "Cfg.ini")
    print(f"OPT: {len(cfg.opt)} keys")
    print(f"[FN] {len(cfg.fn)} mappings")
    for idx, be in cfg.fn_entries:
        print(f"  K{idx:<3} = {', '.join(f'0x{b:02X}' for b in be)}")
    print(f"[KEY] {len(cfg.keys)} keys")
    for idx, e in cfg.key_entries:
        print(f"  K{idx:<3} = {e.matrix} {', '.join(f'0x{b:02X}' for b in e.behavior)}")