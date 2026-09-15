"""Analysis of USB captures of the K617 OEM software.

Inputs accepted:
  * Wireshark JSON : `tshark -r cap.pcapng -T json > cap.json` (recommended)
  * raw .pcap     : Linux usbmon capture, parsed with dpkt
  * .pcapng       : converted once by tshark on the machine that has it

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

from k617_protocol import frame_kind

SET_REPORT = 0x09
GET_REPORT = 0x01

_HEX = re.compile(r"^[0-9a-fA-F\s]+$")


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
        return bytes.fromhex(s) if _HEX.match(s) else None
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
        # some frames carry nested usb layers → normalise to a list
        usb_layers = usb if isinstance(usb, list) else [usb]
        for u in usb_layers:
            if not isinstance(u, dict):
                continue
            fc = _parse_usb_layer(u)
            if fc is not None:
                out.append(fc)
    return out


def _parse_usb_layer(u: dict) -> FrameCapture | None:
    src = _first(u.get("usb.src"), 0)
    dst = _first(u.get("usb.dst"), 0)
    transfer = _first(u.get("usb.transfer_type"), 0)
    transfer = {"0x02": "control", "0x03": "interrupt"}.get(transfer, str(transfer))

    data = _as_bytes(_first(u.get("usb.data_fragment"), 0))
    if not data:
        return None

    setup = u.get("usb.setup") or {}
    if isinstance(setup, list):
        setup = setup[0] if setup else {}
    request = None
    if isinstance(setup, dict):
        key = setup.get("usb.bRequest") or setup.get("usb.bRequest") or setup.get("usb.setup.bRequest") or setup.get("usb.bRequest")
        if key is not None:
            request = int(key, 16) if isinstance(key, str) else int(key)
    report_id = None
    if isinstance(setup, dict) and request is not None:
        wv = setup.get("usb.setup.wValue.byte0") or setup.get("usb.wValue.byte0")
        if wv is not None:
            report_id = int(wv, 16) if isinstance(wv, str) else int(wv)

    direction = "OUT"
    bmrt = _first(u.get("usb.bmRequestType"), 0)
    if isinstance(bmrt, str):
        direction = "OUT" if (int(bmrt, 16) & 0x80) == 0 else "IN"
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


def load_pcap_usbmon(path: str) -> list[FrameCapture]:
    """Minimal Linux usbmon .pcap reader (dpkt)."""
    import dpkt

    out: list[FrameCapture] = []
    with open(path, "rb") as fh:
        pcap = dpkt.pcap.Reader(fh)
        for _, buf in pcap:
            try:
                u = dpkt.usbmon.LinuxUSB(buf)
            except Exception:
                continue
            data = bytes(u.transfer_buffer) if u.xfer_type & 0x80 else None
            # reconstruct direction from the URB type
            direction = "IN" if u.urb_type == 0x55 else "OUT"
            out.append(FrameCapture(direction=direction, transfer="urb", request=None, report_id=None, data=data or b""))
    return out


def significant(records: list[FrameCapture], direction: str = "OUT") -> list[FrameCapture]:
    """Keep only payload-bearing writes from the host (the interesting direction)."""
    return [r for r in records if r.direction == direction and len(r.data) > 0]


def diff_captures(a: list[FrameCapture], b: list[FrameCapture]) -> list[dict]:
    """Align two captures and report the changed byte positions.

    The captures are aligned in order (the OEM software sends the same
    number of blocks every time). Returns per-frame reports.
    """
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