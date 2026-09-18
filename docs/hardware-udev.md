# Hardware, udev, and the quirks that cost real time

Runtime/hardware facts not visible in the encoders.  `src/fizzctl/udev_rules.py`
and `src/fizzctl/hid.py` implement what's described here.

## Identity & interfaces

`vid:pid = 258a:0049` (Redragon / BY Tech 60%).  Two HID interfaces:

- **interface 0** — boot keyboard / media keys (standard HID).
- **interface 1** — vendor-defined usage page `0xFF00`, report IDs
  `0x05`/`0x06`/`0x08`.  **Always talk to interface 1**; `K617._open()`
  enumerates and picks the `interface_number == 1` path.

If the keyboard is in wireless/Bluetooth mode it may not appear at all — the
error message reminds the user to check.

## udev rules (`fizzctl setup-udev`)

`99-k617.rules` (installed to `/etc/udev/rules.d/`, one `sudo`):

```
SUBSYSTEM=="hidraw", ATTRS{idVendor}=="258a", ATTRS{idProduct}=="0049", MODE="0666", TAG+="uaccess"
SUBSYSTEM=="usb",    ATTRS{idVendor}=="258a", ATTRS{idProduct}=="0049", MODE="0666", TAG+="uaccess"
SUBSYSTEM=="input",  ATTRS{idVendor}=="258a", ATTRS{idProduct}=="0049", MODE="0660", TAG+="uaccess"
KERNEL=="event*",    ATTRS{idVendor}=="258a", ATTRS{idProduct}=="0049", MODE="0660", TAG+="uaccess"
```

Because `fizzctl` is often installed in a user-local path (`~/.local/bin`),
`sudo fizzctl …` would fail with "command not found".  So `setup-udev`
**elevates only the privileged syscalls** in one `sudo sh -c` run (install the
rules file, `udevadm control --reload-rules`, `udevadm trigger`) and prompts
for the password itself.  After it runs, unplug/replug the keyboard if the
re-trigger didn't pick the device up.

Rules are slipped into the workflow transparently: `open_device()` catches a
"found but can't open" (`UdevRequiredError`) and offers to run the install
now, retrying the open up to 8× (udev permission changes settle slowly) with
1 s sleeps.

## The single-open quirk (biggest time sink this session)

Only **one handle to the vendor interface can be open at a time**.  If a
second open happens while one is alive it fails with a message that looks
exactly like a permission problem:

```
Found your K617 but it could not be opened (3-7:1.1: open failed).
Your user lacks permission to access it. ...
```

— even though udev rules are fine and root would get the same error.  How it
bit us: a test script held a `K617` device open while invoking a **second**
open (e.g. `cmd_macro`) → spurious "open failed".  Real `fizzctl` invocations
are separate processes that open, talk, close — never affected.

Lessons:
- Never hold the device open across calls that open it again in-process.
- Sequential subprocess invocations (as real usage does) are safe.
- During rapid open/close loops, occasional transient `open failed`
  (exit code 1) can appear even through normal use — space the opens out and
  retry rather than diagnosing permissions.

## Read reliability

- Reads are select-then-read with a ~60 ms settle (see `protocol.md`).
- Reading immediately after a flash write can return stale data; re-read.
- Handshake rule for rgb/effect bursts: mandatory GET after INIT, or the burst
  is silently dropped.

## Capturing the OEM (workflow)

USB captures were taken on Windows with USBPcap, exported as tshark JSON into
`captures/`, and processed with the `fizzctl-dev` toolkit (see
`re-methodology.md`).  The `captures/` dir is the ground truth: cfg_r1..r5,
per-key `paint`/`rgb`/`animate` runs, snake/rainbow in 3 colors, and an
extensive `*macro*` set (adding/removing/applying, cycle counts, until
released, two macro keys on Alt+0).