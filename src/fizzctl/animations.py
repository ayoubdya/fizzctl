"""Host-side per-key animations streamed at N fps via the Sinodragon protocol.

These are NOT firmware effects — they render color maps on the host and push
one 382-byte per-key report per frame (volatile; stop with Ctrl+C).  Only the
8 firmware-native effects (`fizzctl effect`) survive a disconnect.

`fizzctl animate <name> --daemon` runs the stream in a detached background
daemon (PID in <XDG_RUNTIME_DIR|/tmp>/fizzctl-animate.pid) so the terminal is
free; `fizzctl animate stop` ends it.

Animation helpers are pure ("render_frame(t, color, speed) -> dict") so they
can be unit-tested without hardware.
"""
from __future__ import annotations

import math
import os
import signal
import time

from .effects import PER_KEY_POS, encode_per_key_frame, parse_color

ANIMATIONS = ["solid", "blink", "pulse", "chase", "wave", "rainbow", "drop"]


def hsv(h: float, s: float, v: float) -> tuple[int, int, int]:
    """HSV (h 0..360, s/v 0..1) -> (r,g,b) 0..255."""
    c = v * s
    x = c * (1 - abs((h / 60) % 2 - 1))
    m = v - c
    if h < 60:
        r, g, b = c, x, 0
    elif h < 120:
        r, g, b = x, c, 0
    elif h < 180:
        r, g, b = 0, c, x
    elif h < 240:
        r, g, b = 0, x, c
    elif h < 300:
        r, g, b = x, 0, c
    else:
        r, g, b = c, 0, x
    return int((r + m) * 255), int((g + m) * 255), int((b + m) * 255)


def render_frame(t: float, anim: str, color: tuple[int, int, int],
                 speed: float = 1.0) -> dict[str, tuple[int, int, int]]:
    """Produce a color map for animation `anim` at time `t` (seconds).

    speed scales the cycle period (higher = faster).  Positions are the
    verified col*6+row raster from effects.PER_KEY_POS.
    """
    r, g, b = color
    out: dict[str, tuple[int, int, int]] = {}
    period = max(0.1, 2.0 / speed)

    if anim == "solid":
        return {k: color for k in PER_KEY_POS}

    if anim == "blink":
        cycle = (t / period) % 1
        return {k: color if cycle < 0.5 else (0, 0, 0) for k in PER_KEY_POS}

    if anim == "pulse":
        v = 0.5 + 0.5 * math.sin(2 * math.pi * t / period)
        dim = tuple(int(c * v) for c in color)
        return {k: dim for k in PER_KEY_POS}

    if anim == "chase":
        order = sorted(PER_KEY_POS, key=lambda k: PER_KEY_POS[k])
        idx = int((t / period) * len(order)) % len(order)
        for i, k in enumerate(order):
            dist = (i - idx) % len(order)
            frac = max(0.0, 1.0 - dist / 5)
            out[k] = tuple(int(c * frac) for c in color) if dist < 5 else (0, 0, 0)
        return out

    if anim == "wave":
        for k, pos in PER_KEY_POS.items():
            col = pos // 6
            phase = (t / period + col / 14) % 1
            v = 0.5 + 0.5 * math.sin(2 * math.pi * phase)
            out[k] = tuple(int(c * v) for c in color)
        return out

    if anim == "rainbow":
        order = sorted(PER_KEY_POS, key=lambda k: PER_KEY_POS[k])
        for i, k in enumerate(order):
            hue = (360 * t / period + 360 * i / len(order)) % 360
            out[k] = hsv(hue, 1.0, 1.0)
        return out

    if anim == "drop":
        for k, pos in PER_KEY_POS.items():
            col = pos // 6
            row = pos % 6
            phase = ((t * speed + col) % 6) - row
            out[k] = color if 0 <= phase < 1 else (0, 0, 0)
        return out

    raise ValueError(f"unknown animation {anim!r}; choose from {', '.join(ANIMATIONS)}")


def run_animation(dev, anim: str, color: tuple[int, int, int],
                  fps: int = 30, speed: float = 1.0, duration: float | None = None) -> None:
    """Stream `anim` until Ctrl+C (or `duration` seconds)."""
    from .hid import send_per_key

    period = 1.0 / fps
    start = time.monotonic()
    try:
        while True:
            if duration is not None and time.monotonic() - start >= duration:
                break
            frame = encode_per_key_frame(render_frame(time.monotonic() - start, anim, color, speed))
            send_per_key(dev, frame)
            time.sleep(period)
    except KeyboardInterrupt:
        pass


def cmd_animate(args):
    """fizzctl animate <name> [--color HEX] [--speed N] [--fps N] [--duration S] [--daemon]"""
    if args.name == "stop":
        return _animate_stop()
    anim = args.name
    if anim is None:
        print("Host-side animations (volatile — lost on disconnect). Run like:")
        print("  fizzctl animate chase --color yellow --speed 2 --fps 30")
        print("  fizzctl animate chase --daemon   # background daemon, `animate stop` ends it")
        for name in ANIMATIONS:
            print(f"  {name}")
        return 0
    if anim not in ANIMATIONS:
        print(f"unknown animation {anim!r}. Available: {', '.join(ANIMATIONS)}")
        return 1
    color = parse_color(args.color or "ff0000")
    if color is None:
        print(f"bad color {args.color!r}")
        return 1
    if getattr(args, "daemon", False) and not getattr(args, "daemon_child", False):
        return _animate_daemon(args, anim, color)
    from .hid import NoDeviceError, open_device
    try:
        dev = open_device(debug=getattr(args, 'debug', False))
    except NoDeviceError:
        return 1
    if dev is None:
        return 1
    try:
        print(f"streaming {anim} at {args.fps}fps (Ctrl+C to stop)...")
        run_animation(dev, anim, color, fps=args.fps, speed=args.speed, duration=args.duration)
    finally:
        dev.close()
        if getattr(args, "daemon_child", False):
            try:
                os.unlink(_pidfile())
            except OSError:
                pass
    return 0


def _pidfile() -> str:
    d = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
    return os.path.join(d, "fizzctl-animate.pid")


def _alive(pid: int) -> bool:
    """True only if pid is a plausible live pid (bounds guard the SIGTERM path)."""
    if not 1 <= pid <= 4194304:                 # linux pid_max default
        return False
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, OverflowError):
        return False
    return True


def _animate_daemon(args, anim, color):
    """Stream `anim` in a detached background daemon; returns 0 when started.

    The stream runs in a *fresh* interpreter (re-exec via `python -m fizzctl`)
    rather than a fork() so the hidapi/libusb state and inherited file
    descriptors of the launcher never leak into the loop — a forked child can
    open the device yet silently render nothing.
    """
    import subprocess
    import sys

    pidfile = _pidfile()
    try:
        with open(pidfile) as f:
            old = int(f.read().strip())
    except (OSError, ValueError):
        old = None
    if old is not None and _alive(old):
        print(f"animate already running (pid {old}); `fizzctl animate stop` first")
        return 1

    cmd = [sys.executable, "-m", "fizzctl"]
    if getattr(args, "debug", False):
        cmd.append("--debug")
    cmd += ["animate", anim, "--daemon-child", "--color", args.color,
            "--speed", str(args.speed), "--fps", str(args.fps)]
    if args.duration is not None:
        cmd += ["--duration", str(args.duration)]
    try:
        child = subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL,
                                 start_new_session=True, close_fds=True)
    except OSError as e:
        print(f"animate failed to start: {e}")
        return 1
    with open(pidfile, "w") as f:
        f.write(str(child.pid))
    print(f"streaming {anim} in background (pid {child.pid}); fizzctl animate stop to end")
    return 0


def _animate_stop():
    pidfile = _pidfile()
    try:
        with open(pidfile) as f:
            pid = int(f.read().strip())
    except (OSError, ValueError):
        print("animate is not running")
        return 0
    if not _alive(pid):
        try:
            os.unlink(pidfile)
        except OSError:
            pass
        print("animate is not running")
        return 0
    os.kill(pid, signal.SIGTERM)
    for _ in range(100):
        if not _alive(pid):
            try:
                os.unlink(pidfile)
            except OSError:
                pass
            print("stopped")
            return 0
        time.sleep(0.05)
    print(f"animate did not stop; kill pid {pid} manually")
    return 1