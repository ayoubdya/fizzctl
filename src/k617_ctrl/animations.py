"""Host-side per-key animations streamed at N fps via the Sinodragon protocol.

These are NOT firmware effects — they render color maps on the host and push
one 382-byte per-key report per frame (volatile; stop with Ctrl+C).  Only the
8 firmware-native effects (`k617-ctrl effect`) survive a disconnect.

Animation helpers are pure ("render_frame(t, color, speed) -> dict") so they
can be unit-tested without hardware.
"""
from __future__ import annotations

import math
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
    """k617-ctrl animate <name> [--color HEX] [--speed N] [--fps N] [--duration S]"""
    from .hid import NoDeviceError, open_device

    anim = args.name
    if anim not in ANIMATIONS:
        print(f"unknown animation {anim!r}. Available: {', '.join(ANIMATIONS)}")
        return 1
    color = parse_color(args.color or "ff0000")
    if color is None:
        print(f"bad color {args.color!r}")
        return 1
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
    return 0