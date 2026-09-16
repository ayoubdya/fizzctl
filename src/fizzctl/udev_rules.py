"""Udev rules for the Redragon K617 Fizz (VID 258a, PID 0049).

The rules grant the current user access to the vendor HID interface(s) via the
``uaccess`` tag (systemd-logind) and a permissive mode so ``fizzctl`` works
without root.  Install with ``fizzctl setup-udev`` — the command escalates
its own privileged steps via ``sudo`` (prompting for a password if needed),
so you do not need ``sudo fizzctl`` itself.
"""
from __future__ import annotations

import os
import shutil
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


def _is_root() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0


def _reload_udev() -> None:
    """Reload udev rules and re-trigger device events (needs root)."""
    res = subprocess.run(["udevadm", "control", "--reload-rules"], capture_output=True, text=True)
    if res.returncode != 0:
        print(f"warning: udevadm reload failed ({res.stderr.strip() or res.returncode})")
        print("unplug/replug the keyboard, or run: sudo udevadm trigger")
        return
    subprocess.run(["udevadm", "trigger"], capture_output=True)


def _install_as_root(target: str) -> int:
    try:
        os.makedirs(UDEV_DIR, exist_ok=True)
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(RULES)
        print(f"installed udev rules -> {target}")
    except PermissionError as e:
        print(f"error: cannot write {target}: {e}")
        return 1
    _reload_udev()
    return 0


def install_udev_rules(dry_run: bool = False) -> int:
    """Install the udev rules file and reload udev (elevating via sudo)."""
    target = os.path.join(UDEV_DIR, UDEV_FILE)
    if dry_run:
        print(f"[dry-run] would install udev rules to {target}")
        print(f"[dry-run] rules:\n{RULES}")
        return 0

    if _is_root():
        return _install_as_root(target)

    # Not root: escalate the privileged steps.  The binary is often installed
    # in a user-local path (e.g. ~/.local/bin), so `sudo fizzctl` would fail
    # with "command not found" — instead we only elevate the syscalls that need
    # root, in a single sudo session (one password prompt).
    if shutil.which("sudo") is None:
        print("error: not running as root and `sudo` is not available")
        return 1

    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix=".rules", delete=False, prefix="k617-",
    ) as fh:
        fh.write(RULES)
        src = fh.name
    try:
        cmd = (
            f"install -o root -g root -m 0644 {src!r} {target!r} "
            "&& udevadm control --reload-rules "
            "&& udevadm trigger"
        )
        print("elevating to root via sudo to install udev rules…")
        res = subprocess.run(["sudo", "sh", "-c", cmd])
        if res.returncode != 0:
            print("error: sudo install/udevadm failed (see output above)")
            return 1
    finally:
        os.unlink(src)

    print(f"installed udev rules -> {target}")
    return 0