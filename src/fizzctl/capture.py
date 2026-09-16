"""USB capture import/diff/export for the K617 OEM software.

Input accepted:
  * Wireshark JSON : `tshark -r cap.pcapng -T json > cap.json` (recommended)

The job:
  1. pull every HID feature-report payload out of the USB stream,
  2. label it (INIT / CANVAS / ROUTING / EXEC / unknown),
  3. diff two captures so a one-key config change stands out.

Class identifiers:
  OUT  = host -> device SET_REPORT (a config write)
  IN   = device -> host GET_REPORT response (firmware contents / ack)
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .protocol import frame_kind

SET_REPORT = 0x09
GET_REPORT = 0x01

_HEX = re.compile(r"^[0-9a-fA-F\s:]+$")


@dataclass
class FrameCapture:
    """One captured USB payload, normalised for diffing/replay."""

    direction: str          # "OUT" (host->dev) or "IN" (dev->host)
    transfer: str           # "control" / "interrupt"
    request: int | None     # HID bRequest: 0x09 SET_REPORT / 0x01 GET_REPORT
    report_id: int | None   # from wValue low byte
    data: bytes = b""
    meta: dict = field(default_factory=dict)

    def __repr__(self):
        kind = frame_kind(self.data)
        return f"{self.direction:>3} {self.transfer:<9} req={self.request} rid={self.report_id} {kind} {len(self.data)}B"


def _as_bytes(value) -> bytes | None:
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        if _HEX.match(s):
            return bytes.fromhex(s.replace(":", ""))
        try:
            return s.encode("utf-8", "replace")
        except Exception:
            return None
    if isinstance(value, list):
        return bytes(int(x) for x in value)
    return None


def _first(it, n: int, default=None):
    if isinstance(it, list):
        return it[n] if len(it) > n else default
    return default if it is None else it


def load_tshark_json(path: str) -> list[FrameCapture]:
    """Parse `tshark -T json` output into FrameCapture records."""
    with open(path) as fh:
        packets = json.load(fh)
    out: list[FrameCapture] = []
    for pkt in packets:
        layers = pkt.get("_source", {}).get("layers", {})
        usb = layers.get("usb")
        if not usb:
            continue
        usb_layers = usb if isinstance(usb, list) else [usb]
        setup = layers.get("Setup Data")
        for u in usb_layers:
            if not isinstance(u, dict):
                continue
            fc = _parse_usb_layer(u, setup)
            if fc is not None:
                out.append(fc)
    return out


def _parse_usb_layer(u: dict, setup: dict | None = None) -> FrameCapture | None:
    src = _first(u.get("usb.src"), 0)
    dst = _first(u.get("usb.dst"), 0)
    transfer = _first(u.get("usb.transfer_type"), 0)
    transfer = {"0x02": "control", "0x03": "interrupt"}.get(transfer, str(transfer))

    data = _as_bytes(u.get("usb.data_fragment"))
    if not data:
        data = _as_bytes(u.get("usb.capdata"))
    if not data and setup:
        data = _as_bytes(setup.get("usb.data_fragment"))
    if not data:
        return None

    setup = setup or {}
    request = None
    for key in ("usb.bRequest", "usb.setup.bRequest", "usbhid.setup.bRequest"):
        key = setup.get(key) or u.get(key)
        if key is not None:
            request = int(key, 16) if isinstance(key, str) else int(key)
            break

    report_id = None
    if request is not None:
        wv = (setup.get("usbhid.setup.wValue") or setup.get("usb.setup.wValue")
              or setup.get("usb.wValue"))
        if wv is None:
            tree = setup.get("usbhid.setup.wValue_tree")
            if isinstance(tree, dict):
                rid = tree.get("usbhid.setup.ReportID")
            else:
                rid = None
            wv = rid
        if wv is not None:
            v = int(str(wv).replace("0x", ""), 16) if isinstance(wv, str) else int(wv)
            report_id = (v & 0xFF) if v <= 0xFFFF else (int(str(wv).split()[1]) & 0xFF)

    direction = "OUT"
    bmrt = _first(u.get("usb.bmRequestType"), setup.get("usb.bmRequestType"), 0)
    if bmrt is not None:
        if isinstance(bmrt, str) and "0x" in bmrt:
            direction = "OUT" if (int(bmrt, 16) & 0x80) == 0 else "IN"
        elif ":" in str(bmrt):
            direction = "OUT" if (int(str(bmrt)[:4], 16) & 0x80) == 0 else "IN"
        else:
            direction = "OUT" if src == "host" else ("IN" if dst == "host" else "OUT")
    else:
        direction = "OUT" if src == "host" else ("IN" if dst == "host" else "OUT")

    return FrameCapture(
        direction=direction,
        transfer=transfer,
        request=request,
        report_id=report_id,
        data=data,
        meta={"frame": _first(u.get("usb.frame.number"), 0)},
    )


def significant(records: list[FrameCapture], direction: str = "OUT") -> list[FrameCapture]:
    """Keep only payload-bearing writes from the host (the interesting direction)."""
    return [r for r in records if r.direction == direction and len(r.data) > 0]


def diff_captures(a: list[FrameCapture], b: list[FrameCapture]) -> list[dict]:
    """Align two captures and report the changed byte positions."""
    order_a = significant(a)
    order_b = significant(b)
    changes = []
    n = min(len(order_a), len(order_b))
    for i in range(n):
        fa, fb = order_a[i], order_b[i]
        if fa.data == fb.data:
            continue
        diffs = []
        for j, (ca, cb) in enumerate(zip(fa.data, fb.data)):
            if ca != cb:
                diffs.append((j, f"{ca:02x}", f"{cb:02x}"))
        head = diffs[:24]
        changes.append({
            "frame": i,
            "kind_a": frame_kind(fa.data),
            "kind_b": frame_kind(fb.data),
            "len_a": len(fa.data),
            "len_b": len(fb.data),
            "diff_count": len(diffs),
            "offsets": head,
            "unique_a": next((d for d in diffs if d[0] not in {x[0] for x in head}), None),
            "data": fb.data if len(fb.data) == len(fa.data) else b"",
        })
    return changes


def export_frames(records: list[FrameCapture], path: str, direction: str = "OUT") -> None:
    """Write host->device SET_REPORT payloads to a JSON file for replay."""
    frames = [{"direction": r.direction, "data_hex": r.data.hex()} for r in significant(records, direction)]
    with open(path, "w") as fh:
        json.dump(frames, fh, indent=1)
    print(f"wrote {len(frames)} frames -> {path}")


def load_frames(path: str) -> list[bytes]:
    """Load frames written by export_frames()."""
    with open(path) as fh:
        items = json.load(fh)
    return [bytes.fromhex(i["data_hex"]) for i in items if i.get("direction") == "OUT"]