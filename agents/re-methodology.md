# Reverse-engineering methodology, verified facts, lessons, unknowns

How the K617 protocol was cracked, what we proved on real hardware, and what
is still unproven.  The dev toolkit is `fizzctl-dev`
(`inspect`/`cfg`/`diff`/`export`/`replay`, in `src/fizzctl/`).

## Method

1. **Capture the vendor software.**  USBPcap on Windows → tshark JSON →
   `captures/*.json`.  `fizzctl-dev inspect` summarizes the host→device
   writes (`significant()` keeps only outbound frames).
2. **Diff two tiny changes.**  `fizzctl-dev diff A B` shows the exact bytes
   that move.  This cracked almost everything:
   - effect id: applying 8 different effects only changed `EXEC[21]`, and the
     values matched their 1-indexed menu positions → the rest of the 22 were
     inferred by position (`pending-live-verify`).
   - speed/brightness: settled on `EXEC[39]` (live) **and** the per-effect
     memory table `EXEC[39 + 2*(id-1)]`.
   - color: red/green/blue runs of the *same* effect differ only in its MODE
     color slot; which slot is effect-specific (fixed-on [29..31], sine-wave
     [281..283], default [218..220]).
   - per-key paint positioning: Esc=1, Menu=77, RCtrl=83 (earlier guesses
     Esc=0/Menu=65/RCtrl=71 were dead positions).
3. **Live-probe the read path.**  Found that a `05 8x xx 00 00 00` selector
   plus `GET_REPORT(0x06, 1032)` returns the block with a read-flipped header
   (`06 88 b8` for MODE etc.).  The keymap selector `05 84 d4` and macro
   selector `05 85 dc` were discovered by mirroring the known lighting ones.
4. **Decode against captured examples.**  Macro event bytes (`delay | 0x80
   release`) were nailed by matching the OEM's recorded macros byte-for-byte;
   the reader now cross-checks against 7 hardware samples.
5. **Hardware-verify and re-verify.**  Every offset below was confirmed on the
   actual keyboard, and the fix for the layout bug was re-run through the exact
   user repro (bind → flash stock → confirm Alt keeps its macro).

## Verified on hardware (checklist)

- Keymap read & macro read return live state (remaps echoed; slots intact).
- Macro→key bindings survive keymap flashes when relocated by physical key;
  `--read` shows key name + mode per slot.
- Binding offsets: "0"→576 (FN slot 10), CapsLock→616 (slot 20), LAlt→660
  (slot 31) under cfg_final; LAlt→76 (plain col 17) under stock.ini.
- Effect color slots: fixed-on MODE[29..31] green; snake MODE[218..220] blue.
- Speed is stored 0-based (display += 1); sine-wave `--speed 1` → shows 2.
- Per-key: Esc=1, Menu=77, RCtrl=83.
- Media codes: play/pause cfg `0x22` → wire `0xcd` (OEM capture cfg_r4).
  Full enum (`0x22` play/pause, `0x23` stop, `0x24` prev, `0x25` next,
  `0x26` vol+, `0x27` vol-, `0x28` mute) cross-checked against the shared
  `redragonKB-remap` enum; `0x26/0x27/0x28` match our captures.
- until-released mode (`10 00 04 <slot>`) vs cycles (`10 00 01 <slot>`) echo
  back from the device and persist across writes.
- Single-open HID: second concurrent open fails ("open failed", misread as
  permission) — fixed by sequential separate processes.

## Bugs/regressions we introduced and caught

- **Raw-offset binding copy** — flashing `stock.ini` killed Alt macros
  (layout moved Alt from an FN slot to a plain column).  → `relocate_bindings`.
- **Color silently ignored** — baked `0x07` multicolor toggles + wrong global
  slot (`EXEC[56]` is only snake's).  → per-effect toggle `EXEC[38+2*(id-1)]`.
- **Speed off-by-one** — stored display value as-is.  → store `speed-1`.
- **Writes resetting lighting/other macros** — before read-back existed, each
  write used baked frames/empty tables.  → read-then-echo (lighting, keymap,
  macro) on every write; `remove-all` restores bindings from the oracle.
- **State-file illusion** — a local state file drifted from the device.  →
  dropped entirely (`state.py` removed), the device is the single source of
  truth.
- **Transient process bugs** — double-open in test scripts (see
  `hardware-udev.md`), and 332 vs 32 ms confusion in a macro decode that was
  actually exact.

## Unknowns / not yet proven

- Several effect ids beyond the 8 captured are inferred by menu position
  (`pending-live-verify` in `EFFECTS`) — beta status on those.
- Where the "remembered per-effect" brightness lands for ids 21–22; the
  OEM table covers ids 1..20.
- The exact semantics of every special `0x09` FN command (only a few seen).
- Which specific positions beyond the verified ones the 16×6 per-key raster
  uses on the partial/spillover columns.
- COLORDEPTH of MODE render for every effect (only two slots proven).
- MACRO read on a wireless-only connection state was never probed.

## Keep-this-in-repo style rules

- No host-side macro state; read the device before every write
  (`_live_keymap`/`_live_macro`/`_live_lighting`).
- Flash bursts commit (`5A A5`); avoid loops that rewrite identical state.
- When a command can't map a binding to a key it must warn, never silently
  drop — `relocate_bindings` returns warnings the CLI prints.
- Offsets are unstable across layouts; always resolve by physical key, not by
  offset, when copying state between layouts.
- Test with an in-memory fake (`FakeK617`, selector-driven) that mirrors the
  device's read-back — same command must work on real hardware and the fake.