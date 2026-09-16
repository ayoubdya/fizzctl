"""Udev rules for the Redragon K617 Fizz (VID 258a, PID 0049).

The rules grant the current user access to the vendor HID interface(s) via the
``uaccess`` tag (systemd-logind) and a permissive mode so ``k617-ctrl`` works
without root.  Install with ``k617-ctrl setup-udev`` (writes to
``/etc/udev/rules.d/99-k617.rules`` then reloads udev).
"""
from __future__ import annotations

import os
import subprocess
import tempfile

UDEV_DIR = "/etc/udev/rules.d"
UDEV_FILE = "99-k617.rules"

RULES = """\
SUBSYSTEM=="hidraw",  ATTRS{idVendor}=="258a", ATTRS{idProduct}=="0049", MODE="0666", TAG+="uaccess"
SUBSYSTEM=="usb",     ATTRS{idVendor}=="258a", ATTRS{idProduct}=="0049", MODE="0666", TAG+="uaccess"
SUBSYSTEM=="input",   ATTRS{idVendor}=="258a", ATTRS{idProduct}=="0049", MODE="0660", TAG+="uaccess"
KERNEL=="event*",     ATTRS{idVendor}=="258a", ATTRS{idProduct}=="0049", MODE="0660", TAG+="uaccess"
"""


def install_udev_rules(dry_run: bool = False) -> int:
    """Install the udev rules file and reload udev.  Needs root."""
    target = os.path.join(UDEV_DIR, UDEV_FILE)
    if dry_run:
        print(f"[dry-run] would write udev rules to {target}")
        print(f"[dry-run] rules:\n{RULES}")
        return 0

    try:
        if os.access(UDEV_DIR, os.W_OK):
            with open(target, "w", encoding="utf-8") as fh:
                fh.write(RULES)
        else:
            # No write permission on /etc/udev — escalate via tee/install.
            with tempfile.TemporaryDirectory() as td:
                src = os.path.join(td, UDEV_FILE)
                with open(src, "w", encoding="utf-8") as fh:
                    fh.write(RULES)
                subprocess.run(
                    ["install", "-o", "root", "-g", "root", "-m", "0644", src, target],
                    check=True,
                )
        print(f"installed udev rules -> {target}")
    except (PermissionError, subprocess.CalledProcessError) as e:
        print(f"error: cannot write {target}: {e}")
        print("run with sudo instead:  sudo k617-ctrl setup-udev")
        return 1

    print("reloading udev rules…")
    res = subprocess.run(
        ["udevadm", "control", "--reload-rules"], capture_output=True, text=True
    )
    if res.returncode != 0:
        print(f"warning: udevadm reload failed ({res.stderr.strip() or res.returncode})")
        print("unplug/replug the keyboard, or run: sudo udevadm trigger")
        return 0  # rules are installed; reload failure is non-fatal
    subprocess.run(["udevadm", "trigger"], capture_output=True)
    return 0