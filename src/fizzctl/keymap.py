"""K617 KEYMAP block (06 04 d4) encoder.

The keymap block is 1032 bytes = 8-byte command header +
256 four-byte records.  Records are indexed by MATRIX COLUMN and
hold, for each physical key, what the key SENDS as a USB HID usage code.

Layout (all offsets relative to block start):
  +0x00  header        06 04 d4 00 40 00 00 00
  +0x08  col records   256 slots * 4B; slot n holds the record for the
                        matrix column n (slot = column index, 0-based:
                        offset = 8 + col*4).  Content by key type:
                          normal key   -> 00 00 00 <HID>
                          modifier     -> 06 00 00 <HID>
                          has FN layer -> 02 00 00 <FN index>
                          Fn key       -> 20 00 00 00
                          unassigned   -> 00 00 00 00
  +0x218 region B      FN keys' BASE-layer output, in row-major physical
                        order (top row -> bottom row, left -> right):
                          normal -> 00 00 00 <HID>   modifier -> 06 00 00 <HID>
  +0x2d8 region C      FN keys' FN-layer output, same order as region B:
                          normal FN -> 00 00 00 <HID>
                          media FN  -> 04 00 00 <media code>
                          special   -> 4B big-endian value (type 9 fns)

The FN index stored in the col records is the 0-based position of that
key in the region-B/C row-major list.
"""

from dataclasses import dataclass

HEADER = bytes.fromhex("0604d40040000000")

# Windows VK code -> USB HID keyboard usage code, for the K617's key set.
VK2HID = {}
VK2HID[0x30] = 0x27                                          # 0
VK2HID.update({0x31 + i: 0x1e + i for i in range(9)})         # 1-9 -> a-row
VK2HID.update({ord('A') + i: 0x04 + i for i in range(26)})      # A-Z
VK2HID.update({0x70 + i: 0x3a + i for i in range(12)})          # F1-F12
VK2HID.update({
    0x08: 0x2a,   # backspace
    0x09: 0x2b,   # tab
    0x0d: 0x28,   # enter
    0x14: 0x39,   # capslock
    0x1b: 0x29,   # esc
    0x20: 0x2c,   # space
    0x21: 0x4b,   # pageup
    0x22: 0x4e,   # pagedown
    0x23: 0x4d,   # end
    0x24: 0x4a,   # home
    0x25: 0x50,   # left
    0x26: 0x52,   # up
    0x27: 0x4f,   # right
    0x28: 0x51,   # down
    0x2d: 0x49,   # insert
    0x2e: 0x4c,   # delete
    0x2c: 0x46,   # print screen
    0x5b: 0xe3,   # lwin
    0x5d: 0x65,   # rwin (stored as a normal key, not a modifier)
    0xa0: 0xe1,   # lshift
    0xa1: 0xe5,   # rshift
    0xa2: 0xe0,   # lctrl
    0xa3: 0xe4,   # rctrl
    0xa4: 0xe2,   # lalt
    0xa5: 0xe6,   # ralt
    0xa6: 0xe3,   # lwin
    0xba: 0x33,   # ;
    0xbb: 0x2e,   # =
    0xbc: 0x36,   # ,
    0xbd: 0x2d,   # -
    0xbe: 0x37,   # .
    0xbf: 0x38,   # /
    0xc0: 0x35,   # `
    0xdb: 0x2f,   # [
    0xdc: 0x31,   # backslash
    0xdd: 0x30,   # ]
    0xde: 0x34,   # '
})

REGION_B_BASE = 0x218  # FN keys' base-layer outputs
REGION_C_BASE = 0x2d8  # FN keys' fn-layer outputs
N_COLS = 256
N_ROWS = 5              # physical keyboard rows of the 60% layout


@dataclass
class MediaCode:
    """Maps a cfg FN media type-4 code to the on-wire media byte."""

    cfg_code: int
    wire: int
    name: str


# type-4 FN media codes.  The on-wire byte is the USB HID Consumer Page
# usage code (Play/Pause confirmed by capture cfg_r4: cfg 0x22 -> 0xcd,
# which is consumer usage 0x00cd).  The cfg-side enum is the Redragon
# software's fixed list (cross-checked with the shared redragonKB-remap
# enum, which matches our captured 0x26/0x27/0x28): 0x22 play/pause,
# 0x23 stop, 0x24 prev, 0x25 next, 0x26 vol+, 0x27 vol-, 0x28 mute.
MEDIA_CODES = {
    0x22: MediaCode(0x22, 0xcd, "play/pause"),   # captured in cfg_r4
    0x23: MediaCode(0x23, 0xb7, "stop"),
    0x24: MediaCode(0x24, 0xb6, "previous"),
    0x25: MediaCode(0x25, 0xb5, "next"),
    0x26: MediaCode(0x26, 0xe9, "vol+"),
    0x27: MediaCode(0x27, 0xea, "vol-"),
    0x28: MediaCode(0x28, 0xe2, "mute"),
}


class KeymapEncoder:
    """Builds a 1032-byte 06 04 d4 block from a CfgIni."""

    def __init__(self, cfg, vk2hid=None):
        self.cfg = cfg
        self.vk2hid = vk2hid if vk2hid is not None else VK2HID
        self.b = bytearray(1032)
        self.b[0:8] = HEADER

    def _is_mod(self, vk):
        return 0xA0 <= vk <= 0xA6 or vk == 0x5B

    def _row_major_fn_keys(self):
        # order by row (geom y, bucketed by ~36px spacing), then by geom x
        def key(i):
            k = self.cfg.keys[i]
            return (k.geom[1] // 36, k.geom[0])

        return sorted(
            (idx for idx in self.cfg.fn if idx in self.cfg.keys), key=key
        )

    def build(self):
        fn_keys = self._row_major_fn_keys()
        fn_index = {idx: pos for pos, idx in enumerate(fn_keys)}

        # region A: col records, written directly to self.b
        cols = {k.matrix[0]: k for k in (self.cfg.keys[i] for i in self.cfg.keys)}
        for col in range(N_COLS):
            off = 8 + col * 4
            key = cols.get(col)
            if key is None:
                rec = b"\x00\x00\x00\x00"
            else:
                vk = key.behavior[1]
                if vk == 0xFA:                       # Fn key
                    rec = b"\x20\x00\x00\x00"
                elif key.index in fn_index:          # has FN layer
                    rec = bytes([2, 0, 0, fn_index[key.index]])
                elif self._is_mod(vk):
                    rec = bytes([6, 0, 0, self._hid(vk)])
                else:
                    rec = bytes([0, 0, 0, self._hid(vk)])
            self.b[off:off + 4] = rec

        # region B/C in the same row-major order
        for pos, idx in enumerate(fn_keys):
            key = self.cfg.keys[idx]
            vk = key.behavior[1]
            b_off = REGION_B_BASE + pos * 4
            c_off = REGION_C_BASE + pos * 4
            base = bytes([6 if self._is_mod(vk) else 0, 0, 0, self._hid(vk)])
            self.b[b_off:b_off + 4] = base
            self.b[c_off:c_off + 4] = self._fn_output(idx)

        return bytes(self.b)

    def _hid(self, vk):
        return self.vk2hid.get(vk, 0)

    def _fn_output(self, key_idx):
        """4-byte region-C entry for key_idx's FN function."""
        t, code, extra = self.cfg.fn[key_idx]
        if t == 4:                               # media key
            wire = self._media_wire(code)
            return bytes([4, 0, 0, wire])
        if t == 9:                               # special function: packed value
            return extra.to_bytes(4, "big")
        # t == 2 -> normal keycode (VK code) -> HID usage
        return bytes([0, 0, 0, self._hid(code)])

    def _media_wire(self, code):
        m = MEDIA_CODES.get(code)
        return m.wire if m else 0