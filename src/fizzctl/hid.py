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

import os
import time

import hid

from .protocol import PID, VID, frame_kind


class NoDeviceError(Exception):
    """The K617 vendor interface could not be found or opened."""


class UdevRequiredError(NoDeviceError):
    """The HID node exists but cannot be opened — udev rules are missing."""


def udev_rules_installed() -> bool:
    return os.path.exists("/etc/udev/rules.d/99-k617.rules")


class K617:
    def __init__(self, debug: bool = False):
        self.debug = debug
        self._dev = self._open()

    @staticmethod
    def _open():
        try:
            uid = os.geteuid()
        except AttributeError:
            uid = None
        paths = []
        for d in hid.enumerate(VID, PID):
            iface = int(d.get("interface_number") or 0)
            if iface == 1:
                paths.append(d["path"])
        if not paths:
            msg = (
                "Could not find your K617 keyboard. Make sure it is "
                "plugged in (and in wireless mode if it supports it), "
                "then try again."
            )
            if uid != 0:
                msg += (
                    "\nIf it is plugged in, your user may lack permission "
                    "to see it — try running `fizzctl setup-udev` first."
                )
            raise NoDeviceError(msg)
        path = paths[0]
        display = path.decode(errors="replace") if isinstance(path, bytes) else str(path)
        try:
            dev = hid.Device(path=path) if hasattr(hid, "Device") else \
                K617._open_ctypes(path)
            return dev
        except Exception as e:
            if uid != 0 and not udev_rules_installed():
                raise UdevRequiredError(
                    f"Found your K617 but it could not be opened ({display}).\n"
                    "The udev rules that let you access the keyboard without "
                    "sudo are not installed."
                ) from e
            if uid != 0:
                raise NoDeviceError(
                    f"Found your K617 but it could not be opened ({display}: {e}). "
                    "Your user lacks permission to access it. Try running "
                    "`fizzctl setup-udev` (or unplug/replug the keyboard), "
                    "then rerun this command."
                ) from e
            raise NoDeviceError(
                f"Found your K617 but it could not be opened ({display}: {e}). "
                "Is another program controlling the keyboard right now?"
            ) from e

    @staticmethod
    def _open_ctypes(path):
        """Open via the trezor 'hid' ctypes module (hid.device/open_path)."""
        dev = hid.device()
        dev.open_path(path)  # accepts str or bytes
        return dev

    def send_feature(self, data: bytes) -> None:
        """Send an arbitrary feature report (bytes includes the report ID)."""
        self._dev.send_feature_report(data)

    def get_feature(self, report_id: int, size: int) -> bytes:
        """Read a feature report (returns exactly `size` bytes on success)."""
        raw = self._dev.get_feature_report(report_id, size)
        return bytes(raw) if raw is not None else bytes(size)

    def close(self) -> None:
        if self._dev is not None:
            try:
                self._dev.close()
            except Exception:
                pass


def open_device(debug: bool = False) -> K617 | None:
    """Open the K617, prompting to install udev rules when access is denied.

    Returns the device, or None if it could not be opened (the reason is
    already printed). When the rules are missing the user is asked whether
    to install them now, then the open is retried.
    """
    from .udev_rules import install_udev_rules

    try:
        return K617(debug=debug)
    except UdevRequiredError as e:
        print(f"Trouble opening your K617: {e}")
        try:
            answer = input("Install the udev rules now? [Y/n] ").strip().lower()
        except EOFError:
            answer = "y"
        if answer not in ("", "y", "yes"):
            print("Skipped. You can install them later with `fizzctl setup-udev`.")
            return None
        print("Installing udev rules (you may be asked for your password)...")
        try:
            install_udev_rules()
        except Exception as ie:
            print(f"error: could not install udev rules: {ie}")
            return None
        print("udev rules installed. Reopening the keyboard...")
        last_err = None
        for _ in range(8):           # udev permission changes settle slowly
            try:
                return K617(debug=debug)
            except NoDeviceError as e2:
                last_err = e2
                time.sleep(1.0)
        print(f"error: {last_err}")
        print("Hint: unplug and replug the keyboard, then run the command again.")
        return None
    except NoDeviceError as e:
        print(e)
        return None


def rgb_sequence(led_colors: dict[int, tuple[int, int, int]]) -> list[bytes]:
    """Build the RGB-write payloads: [INIT, P1(no SEC yet), P2, EXEC].

    led_colors maps LED index -> (r, g, b). Led indices live in
    protocol.LED_INDEX.

    NOTE: the EXEC frame commits to flash (5AA5 magic). Do not loop this.
    """
    from .blobs import INIT, RGB_EXEC, RGB_SEC
    from .protocol import NAME_TO_INDEX, RESTORE_CONSTANT_FRAMES, set_key_color

    canvas = bytearray(1032)
    canvas[0:5] = bytes.fromhex("0609bc0040")
    for idx, rgb in led_colors.items():
        if isinstance(idx, str):
            idx = NAME_TO_INDEX[idx]
        set_key_color(canvas, idx, rgb)
    canvas[660:660 + len(RGB_SEC)] = RGB_SEC
    p2 = RESTORE_CONSTANT_FRAMES[2]  # routing, never modify
    return [bytes(INIT), bytes(canvas), p2, RGB_EXEC]


def send_burst(dev: K617, frames: list[bytes], handshake: bool = True,
               delay_ms: int = 60) -> None:
    """Send an RGB write burst: INIT -> [mandatory GET handshake] -> blocks.

    ``handshake=True`` (rgb/effect/key/paint): the firmware ignores the burst
    unless we poll GET_REPORT(0x06, 1032) after INIT.  ``handshake=False``
    (keymap/replay) skips it.
    """
    from time import sleep

    for i, frame in enumerate(frames, start=1):
        dev.send_feature(frame)
        if dev.debug:
            print(f"  [{i}/{len(frames)}] {frame_kind(frame)}")
        sleep(delay_ms / 1000)
        if handshake and i == 1:
            resp = dev.get_feature(0x06, 1032)
            if dev.debug:
                print(f"  [handshake] get_feature(0x06, 1032) -> {len(resp)}B")
            sleep(delay_ms / 1000)


def send_per_key(dev: K617, frame: bytes) -> None:
    """Send a single 382-byte per-key report (no handshake needed)."""
    dev._dev.send_feature_report(frame)


# Read selector ("05 8x xx") -> normal write header, for each lighting block.
# The device keeps the current effect/color/keymap-adjacent state in these
# blocks; reading them lets a keymap/macro write echo the live state instead
# of resetting it with the baked stock frames.
_LIGHTING_BLOCKS = (
    ("0588b8000000", bytes.fromhex("0608b80040")),   # MODE
    ("0589bc000000", bytes.fromhex("0609bc0040")),   # CANVAS
    ("0589c0000000", bytes.fromhex("0609c00040")),   # ROUTING
    ("0583b6000000", bytes.fromhex("0603b60000")),   # EXEC
)


def _read_block(dev: K617, selector: str, header: bytes) -> bytes:
    """Select a block with a ``05 8x xx`` frame, read it, restore its header."""
    dev.send_feature(bytes.fromhex(selector))
    time.sleep(0.06)
    raw = dev.get_feature(0x06, 1032)
    frame = bytearray(1032)
    frame[:len(raw)] = raw
    frame[0:5] = header
    return bytes(frame)


def read_lighting(dev: K617) -> list[bytes]:
    """Read the device's current MODE/CANVAS/ROUTING/EXEC blocks, in order.

    Each block is selected with a ``05 8x xx`` INIT frame and read back with
    GET_REPORT(0x06); the read-flag header (e.g. ``06 88 ...``) is restored to
    the normal write header (``06 08 b8 00 40``) so the result can be sent back
    verbatim in a write burst.
    """
    return [_read_block(dev, req, header) for req, header in _LIGHTING_BLOCKS]


def read_keymap(dev: K617) -> bytes:
    """Read the device's current (base) keymap block.

    Only the base layer comes back — macro bindings (``10`` records) are not
    exposed — which is why :mod:`fizzctl.state` caches them separately.  The
    base layer is live, so it does contain your current Cfg.ini remaps.
    """
    return _read_block(dev, "0584d4000000", bytes.fromhex("0604d40040"))


def read_macro(dev: K617) -> bytes:
    """Read the device's macro table block, if the firmware exposes it.

    Mirrors the keymap read (``05 84 d4``) using the macro block id ``dc``.
    The framework may return a fixed/factory table instead of the live one;
    that is exactly what :func:`fizzctl.cli.cmd_read_macro` is for probing.
    """
    return _read_block(dev, "0585dc000000", bytes.fromhex("0605dc0040"))
