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

from .protocol import PID, VID


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

    def send_sequence(self, frames: list[bytes], delay_ms: int = 30) -> None:
        """Send a list of frames sequentially (init -> data -> commit)."""
        from time import sleep

        for i, frame in enumerate(frames):
            kind = _kind(frame)
            self.send_feature(frame)
            if self.debug:
                print(f"  [{i + 1}/{len(frames)}] {kind}")
            sleep(delay_ms / 1000)

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


def _kind(frame: bytes) -> str:
    from .protocol import frame_kind

    return frame_kind(frame)


def rgb_sequence(led_colors: dict[int, tuple[int, int, int]]) -> list[bytes]:
    """Build the RGB-write payloads: [INIT, P1(no SEC yet), P2, EXEC].

    led_colors maps LED index -> (r, g, b). Led indices live in
    protocol.LED_INDEX.

    NOTE: the EXEC frame commits to flash (5AA5 magic). Do not loop this.
    """
    from .blobs import RGB_EXEC, RGB_INIT, RGB_SEC
    from .protocol import NAME_TO_INDEX, RESTORE_CONSTANT_FRAMES, set_key_color

    canvas = bytearray(1032)
    canvas[0:5] = bytes.fromhex("0609bc0040")
    for idx, rgb in led_colors.items():
        if isinstance(idx, str):
            idx = NAME_TO_INDEX[idx]
        set_key_color(canvas, idx, rgb)
    canvas[660:660 + len(RGB_SEC)] = RGB_SEC
    p2 = RESTORE_CONSTANT_FRAMES[2]  # routing, never modify
    return [bytes(RGB_INIT), bytes(canvas), p2, RGB_EXEC]


def rgb_all(color: tuple[int, int, int]) -> list[bytes]:
    from .protocol import LED_INDEX

    return rgb_sequence({idx: color for idx in LED_INDEX})


def send_rgb(dev: K617, frames: list[bytes]) -> None:
    """Send the RGB sequence WITH the mandatory GET_REPORT handshake.

    Protocol: INIT -> GET (handshake) -> P1 -> P2 -> EXEC.
    Without the handshake the firmware silently ignores the writes.
    """
    from time import sleep

    init, canvas, p2, exec_ = frames
    dev.send_feature(init)
    if dev.debug:
        print("  [1/4] INIT")
    sleep(0.06)
    resp = dev.get_feature(0x06, 1032)  # mandatory handshake
    if dev.debug:
        print(f"  [handshake] get_feature(0x06, 1032) -> {len(resp)}B")
    sleep(0.06)
    dev.send_feature(canvas)
    if dev.debug:
        print("  [2/4] CANVAS")
    sleep(0.06)
    dev.send_feature(p2)
    if dev.debug:
        print("  [3/4] ROUTING")
    sleep(0.06)
    dev.send_feature(exec_)
    if dev.debug:
        print("  [4/4] EXEC")


# firmware effects + per-key paint (see fizzctl.effects)
def send_firmware_effect(dev: K617, frames: list[bytes]) -> None:
    """Send the 5-frame firmware-effect burst WITH the mandatory handshake.

    Protocol: INIT -> GET (handshake) -> MODE -> CANVAS -> ROUTING -> EXEC.
    The EXEC block commits the effect selection to flash (5AA5 magic).
    """
    from time import sleep

    init, *blocks = frames
    dev.send_feature(init)
    if dev.debug:
        print("  [1/5] INIT")
    sleep(0.06)
    resp = dev.get_feature(0x06, 1032)  # mandatory handshake
    if dev.debug:
        print(f"  [handshake] get_feature(0x06, 1032) -> {len(resp)}B")
    sleep(0.06)
    for i, block in enumerate(blocks, start=2):
        dev.send_feature(block)
        if dev.debug:
            print(f"  [{i}/5] {_kind(block)}")
        sleep(0.06)


def send_per_key(dev: K617, frame: bytes) -> None:
    """Send a single 382-byte per-key report (no handshake needed)."""
    dev._dev.send_feature_report(frame)
