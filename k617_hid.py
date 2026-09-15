"""Thin hidapi wrapper for the K617 vendor interface (interface 1).

The K617 exposes two HID interfaces:
  * interface 0  — boot keyboard / media keys (standard)
  * interface 1  — vendor-defined usage page 0xFF00, holds the feature
                   reports used by every protocol (report IDs 0x05/0x06/0x08).

We always talk to interface 1 via hidapi's `send_feature_report` /
`get_feature_report`.  These map to USB SET_REPORT / GET_REPORT control
transfers — the exact URBs you will see in USBPcap captures.
"""
from __future__ import annotations

import hid

from k617_protocol import PID, VID


class NoDeviceError(Exception):
    """The K617 vendor interface could not be found or opened."""


class K617:
    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self._dev = None
        if not dry_run:
            self._dev = self._open()

    @staticmethod
    def _open():
        paths = []
        for d in hid.enumerate(VID, PID):
            iface = int(d.get("interface_number") or 0)
            if iface == 1:
                paths.append(d["path"])
        if not paths:
            raise NoDeviceError(
                "K617 vendor interface not found. Is it plugged in? "
                "Run: sudo udevadm control --reload && sudo udevadm trigger"
            )
        dev = hid.Device(path=paths[0]) if hasattr(hid, "Device") else \
            K617._open_ctypes(paths[0])
        return dev

    @staticmethod
    def _open_ctypes(path):
        """Open via the trezor 'hid' ctypes module (hid.device/open_path)."""
        dev = hid.device()
        dev.open_path(path)  # accepts str or bytes
        return dev

    def send_feature(self, data: bytes) -> None:
        """Send an arbitrary feature report (bytes includes the report ID)."""
        if self.dry_run:
            print(f"[dry-run] send_feature({len(data)}B): {data[:16].hex(' ')}...")
            return
        self._dev.send_feature_report(data)

    def get_feature(self, report_id: int, size: int) -> bytes:
        """Read a feature report (returns exactly `size` bytes on success)."""
        if self.dry_run:
            print(f"[dry-run] get_feature(report_id={report_id:#04x}, size={size})")
            return bytes(size)
        raw = self._dev.get_feature_report(report_id, size)
        return bytes(raw) if raw is not None else bytes(size)

    def send_sequence(self, frames: list[bytes], delay_ms: int = 30) -> None:
        """Send a list of frames sequentially (init -> data -> commit)."""
        from time import sleep

        for i, frame in enumerate(frames):
            kind = _kind(frame)
            self.send_feature(frame)
            print(f"  [{i + 1}/{len(frames)}] {kind}")
            sleep(delay_ms / 1000)

    def close(self) -> None:
        if self._dev is not None:
            try:
                self._dev.close()
            except Exception:
                pass


def _kind(frame: bytes) -> str:
    from k617_protocol import frame_kind

    return frame_kind(frame)


# known-good RGB sequence using the captured static-effect template
def rgb_sequence(led_colors: dict[int, tuple[int, int, int]]) -> list[bytes]:
    """Build the 5-frame RGB-write sequence for the given per-key colors.

    led_colors maps LED index -> (r, g, b). Led indices live in
    k617_protocol.LED_INDEX.  This mirrors k617-fizz/k617_rgb.py but loads
    the routing/commit blobs from the captured fw-static template so we never
    hand-construct firmware-critical regions.

    NOTE: the EXEC frame commits to flash (5AA5 magic). Do not loop this.
    """
    from k617_protocol import FRAME_CANVAS, NAME_TO_INDEX, base_frames, set_key_color

    frames = [bytearray(f) for f in base_frames()]
    canvas = frames[FRAME_CANVAS]
    for idx, rgb in led_colors.items():
        if isinstance(idx, str):
            idx = NAME_TO_INDEX[idx]
        set_key_color(canvas, idx, rgb)
    return [bytes(f) for f in frames]


def rgb_all(color: tuple[int, int, int]) -> list[bytes]:
    from k617_protocol import LED_INDEX

    return rgb_sequence({idx: color for idx in LED_INDEX})