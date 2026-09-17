import hashlib
import os
import tempfile
import unittest
from unittest.mock import Mock, call, patch

from fizzctl import blobs, cli
from fizzctl.effects import encode_firmware_effect
from fizzctl.hid import send_burst
from fizzctl.protocol import base_frames


class RegressionTests(unittest.TestCase):
    def setUp(self):
        # keep the macro cache out of the developer's real state file
        self._tmp = tempfile.TemporaryDirectory()
        self._env = patch.dict(os.environ, {"XDG_STATE_HOME": self._tmp.name})
        self._env.start()

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    def test_packet_bytes(self):
        names = ("CONST_MODE", "CONST_CANVAS", "CONST_ROUTING", "CONST_EXEC", "RGB_SEC", "RGB_EXEC")
        data = b"".join(getattr(blobs, name) for name in names) + b"".join(blobs.FW_TEMPLATE)
        self.assertEqual(hashlib.sha256(data).hexdigest(), "d047671cf51c71cdc330ddc5a462a52cc43170254d5ff1739387b52ef0999c23")

    def test_effect_speed_brightness(self):
        exec_ = encode_firmware_effect("rainbow")[4]
        self.assertEqual(exec_[39], 0x33)                     # template default kept
        self.assertEqual(exec_[43], 0x33)                     # its own slot (id 3 -> 39+2*2)
        self.assertEqual(exec_[21], 0x03)

        exec_ = encode_firmware_effect("rainbow", speed=2, brightness=4)[4]
        self.assertEqual(exec_[39], 0x14)                     # speed 2 stores 1 (0-based)
        self.assertEqual(exec_[43], 0x14)                     # slot, not 39+2*id (=45)

        exec_ = encode_firmware_effect("snake", speed=5)[4]   # speed goes to 5, not capped at 4
        self.assertEqual(exec_[39], 0x44)                     # speed 5 stores 4, brightness default 4
        self.assertEqual(exec_[57], 0x44)                     # retro-snake id 0x0a -> 39+2*9

        exec_ = encode_firmware_effect("snake", brightness=9)[4]
        self.assertEqual(exec_[39] & 0x0F, 4)                 # brightness clamps to 0..4

        exec_ = encode_firmware_effect("off")[4]
        self.assertEqual(exec_[39], 0x00)                     # off stays 0x00 when unset
        exec_ = encode_firmware_effect("fixed-on", color=(0, 255, 0), brightness=0)[4]
        self.assertEqual(exec_[39], 0x30)
        self.assertEqual(exec_[21], 0x01)

    def test_effect_color_lands_in_mode_color_field(self):
        # snake uses the default MODE[218..220] color slot (live-verified);
        # eid 0x0a -> flag byte 38+2*9 = EXEC[56]
        frames = encode_firmware_effect("snake", color=(0xff, 0x88, 0x00))
        mode, canvas, exec_ = frames[1], frames[2], frames[4]
        self.assertEqual((mode[218], mode[219], mode[220]), (0xff, 0x88, 0x00))
        self.assertEqual(exec_[56], 0x00)                    # RGB toggle off
        self.assertEqual(bytes(canvas), bytes(base_frames()[2]))

        # sine-wave's slot is different (OEM red/green captures: [281]=R, [282]=G);
        # eid 0x0d -> flag byte 38+2*12 = EXEC[62] (EXEC[56] is snake's slot)
        frames = encode_firmware_effect("sine-wave", color=(0xff, 0x88, 0x00))
        mode, exec_f = frames[1], frames[4]
        self.assertEqual((mode[281], mode[282], mode[283]), (0xff, 0x88, 0x00))
        self.assertEqual(exec_f[62], 0x00)
        self.assertEqual(exec_f[56], 0x07)                   # snake slot stays RGB

        # fixed-on's color slot is MODE[29..31] (live-verified green);
        # eid 0x01 -> flag byte 38+0 = EXEC[38]
        frames = encode_firmware_effect("fixed-on", color=(0x00, 0xff, 0x00))
        mode, exec_f = frames[1], frames[4]
        self.assertEqual((mode[29], mode[30], mode[31]), (0x00, 0xff, 0x00))
        self.assertEqual(exec_f[38], 0x00)

        # no color given -> RGB mode (0x07) kept, canvas untouched
        plain = encode_firmware_effect("sine-wave")
        self.assertEqual(plain[4][62], 0x07)
        self.assertEqual(bytes(plain[2]), bytes(base_frames()[2]))

    @patch("time.sleep")
    def test_send_order(self, sleep):
        frames = blobs.FW_TEMPLATE
        dev = Mock(debug=False)
        dev.get_feature.return_value = bytes(1032)
        send_burst(dev, frames)
        self.assertEqual(dev.mock_calls, [call.send_feature(frames[0]), call.get_feature(6, 1032)] + [call.send_feature(f) for f in frames[1:]])
        self.assertEqual(sleep.call_args_list, [call(0.06)] * 6)
        dev.reset_mock()
        sleep.reset_mock()
        send_burst(dev, frames, handshake=False, delay_ms=30)
        self.assertEqual(dev.mock_calls, [call.send_feature(f) for f in frames])
        self.assertEqual(sleep.call_args_list, [call(0.03)] * 5)
        dev.reset_mock()
        send_burst(dev, [], handshake=False)
        self.assertEqual(dev.mock_calls, [])

    def test_macro_frame_layout(self):
        from fizzctl.macro import encode_event, encode_macro_frame, text_events

        # the OEM's "rgb" macro: 109 down-R, 16 down-G, 63 up-R, 93 up-G,
        # 63 down-B, 3 up-B
        events = [
            encode_event(109, 0x15), encode_event(16, 0x0A),
            encode_event(63, 0x15, True), encode_event(93, 0x0A, True),
            encode_event(63, 0x05), encode_event(3, 0x05, True),
        ]
        frame = encode_macro_frame([(1, events)])
        self.assertEqual(len(frame), 1032)
        self.assertEqual(frame[:5], bytes.fromhex("0605dc0040"))
        self.assertEqual(frame[9], 0x01)                 # slot0 cycle count
        self.assertEqual(frame[10:22], bytes.fromhex("6d15100abf15dd0a3f058305"))

        # text helper: each char = press event + release event
        self.assertEqual(
            text_events("rgb", 30),
            [encode_event(30, 0x15), encode_event(30, 0x15, True),
             encode_event(30, 0x0A), encode_event(30, 0x0A, True),
             encode_event(30, 0x05), encode_event(30, 0x05, True)],
        )

    def test_macro_binding(self):
        from fizzctl.macro import (MODE_CYCLES, MODE_UNTIL_RELEASED,
                                   NAME_TO_HID, bind_macro)

        # offsets verified from captures: key "0"->576, CapsLk->616, LAlt->660
        for name, off in (("0", 576), ("CapsLk", 616), ("LAlt", 660)):
            with self.subTest(key=name):
                b = bytearray(blobs.CONST_KEYMAP)
                self.assertEqual(bind_macro(b, NAME_TO_HID[name], 0, MODE_CYCLES), off)
                self.assertEqual(bytes(b[off:off + 4]), bytes((0x10, 0x00, 0x01, 0x00)))

        # macro slot index lands in the record's byte3; play-mode in byte2
        b = bytearray(blobs.CONST_KEYMAP)
        bind_macro(b, NAME_TO_HID["0"], 0, MODE_CYCLES)
        bind_macro(b, NAME_TO_HID["LAlt"], 1, MODE_CYCLES)
        self.assertEqual(bytes(b[576:580]), bytes((0x10, 0x00, 0x01, 0x00)))
        self.assertEqual(bytes(b[660:664]), bytes((0x10, 0x00, 0x01, 0x01)))
        b = bytearray(blobs.CONST_KEYMAP)
        bind_macro(b, NAME_TO_HID["LAlt"], 0, MODE_UNTIL_RELEASED)
        self.assertEqual(bytes(b[660:664]), bytes((0x10, 0x00, 0x04, 0x00)))

        # unknown key -> no match, keymap untouched
        b = bytearray(blobs.CONST_KEYMAP)
        self.assertIsNone(bind_macro(b, 0x99, 0, MODE_CYCLES))
        self.assertEqual(bytes(b), blobs.CONST_KEYMAP)

    @patch("time.sleep")
    @patch("fizzctl.cli.open_device")
    def test_cli(self, open_device, sleep):
        dev = Mock(debug=False)
        dev.get_feature.return_value = bytes(1032)
        open_device.return_value = dev
        parser = cli._build_parser(False)
        for argv, handler in [(["key", "=", "red"], cli.cmd_key), (["effect", "snake"], cli.cmd_effect), (["rgb", "blue"], cli.cmd_rgb)]:
            with self.subTest(argv=argv):
                dev.reset_mock()
                self.assertEqual(handler(parser.parse_args(argv)), 0)
                dev.close.assert_called_once()
                dev.get_feature.assert_called_once_with(6, 1032)
        open_device.reset_mock()
        self.assertEqual(cli.cmd_paint(parser.parse_args(["paint", "W=invalid"])), 1)
        open_device.assert_not_called()

    @patch("time.sleep")
    @patch("fizzctl.cli.open_device")
    def test_cli_macro(self, open_device, sleep):
        dev = Mock(debug=False)
        dev.get_feature.return_value = bytes(blobs.CONST_KEYMAP)
        open_device.return_value = dev
        parser = cli._build_parser(False)

        # 4 lighting read-selector sends + 1 keymap read-selector send
        def burst():
            return [c.args[0] for c in dev.send_feature.call_args_list][5:]

        rc = cli.cmd_macro(parser.parse_args(["macro", "--key", "CapsLk", "rgb"]))
        self.assertEqual(rc, 0)
        dev.close.assert_called_once()
        # 4 lighting reads + 1 keymap read; no handshake GET after INIT
        self.assertEqual(dev.get_feature.call_count, 5)
        dev.get_feature.assert_called_with(6, 1032)
        frames = burst()
        self.assertEqual([f[:3] for f in frames], [
            bytes.fromhex("0583b6"), bytes.fromhex("0608b8"),
            bytes.fromhex("0609bc"), bytes.fromhex("0609c0"),
            bytes.fromhex("0605dc"), bytes.fromhex("0604d4"),
            bytes.fromhex("0603b6"),
        ])
        self.assertEqual(frames[4][9:22], bytes.fromhex("011e159e151e0a9e0a1e059e05"))
        self.assertEqual(bytes(frames[5][616:620]), bytes((0x10, 0x00, 0x01, 0x00)))
        # LAlt (a different key) is left untouched in the live-base keymap
        self.assertEqual(bytes(frames[5][660:664]), bytes((0x06, 0x00, 0x00, 0xE2)))

        dev.reset_mock()
        rc = cli.cmd_macro(parser.parse_args(
            ["macro", "--key", "LAlt", "--until-released", "--cycles", "3", "hi"]))
        self.assertEqual(rc, 0)
        frames = burst()
        slot1 = 9 + 128
        self.assertEqual(frames[4][slot1], 3)                    # slot1 cycle count
        self.assertEqual(frames[4][9:22], bytes.fromhex("011e159e151e0a9e0a1e059e05"))
        # both macros retained: slot0 (CapsLk) and slot1 (LAlt, until-released)
        self.assertEqual(bytes(frames[5][616:620]), bytes((0x10, 0x00, 0x01, 0x00)))
        self.assertEqual(bytes(frames[5][660:664]), bytes((0x10, 0x00, 0x04, 0x01)))

        open_device.reset_mock()
        self.assertEqual(cli.cmd_macro(parser.parse_args(["macro", "--key", "Nope", "x"])), 1)
        self.assertEqual(cli.cmd_macro(parser.parse_args(["macro", "--key", "A", "@"])), 1)
        self.assertEqual(cli.cmd_macro(parser.parse_args(["macro", "--key", "A", "", "--cycles", "0"])), 1)
        open_device.assert_not_called()

    def test_macro_slot_helpers(self):
        from fizzctl.macro import SLOT_BASE, build_macro_frame, encode_slot

        slot = encode_slot(3, [bytes.fromhex("1e15"), bytes.fromhex("9e15")])
        self.assertEqual(len(slot), 128)
        self.assertEqual(slot[0], 3)
        self.assertEqual(slot[1:5], bytes.fromhex("1e159e15"))
        self.assertEqual(slot[5:], bytes(123))
        frame = build_macro_frame({0: slot})
        self.assertEqual(frame[:5], bytes.fromhex("0605dc0040"))
        self.assertEqual(frame[SLOT_BASE:SLOT_BASE + 5], bytes.fromhex("031e159e15"))
        self.assertEqual(len(frame), 1032)
        with self.assertRaises(ValueError):
            build_macro_frame({8: slot})

    def test_state_alloc_slot(self):
        from fizzctl import state

        st = {"slots": {}, "bindings": {}}
        self.assertEqual(state.alloc_slot(st, "CapsLk"), 0)
        st["slots"]["0"] = "00" * 128
        st["bindings"]["CapsLk"] = {"slot": 0, "mode": 1}
        self.assertEqual(state.alloc_slot(st, "CapsLk"), 0)   # reuse existing
        self.assertEqual(state.alloc_slot(st, "LAlt"), 1)     # next free

    @patch("time.sleep")
    @patch("fizzctl.cli.open_device")
    def test_cli_keymap_reapplies_macros(self, open_device, sleep):
        dev = Mock(debug=False)
        dev.get_feature.return_value = bytes(blobs.CONST_KEYMAP)
        open_device.return_value = dev
        parser = cli._build_parser(False)

        self.assertEqual(cli.cmd_macro(parser.parse_args(["macro", "--key", "CapsLk", "rgb"])), 0)
        dev.reset_mock()
        self.assertEqual(cli.cmd_keymap(parser.parse_args(["keymap", "cfgs/cfg_final.ini"])), 0)
        frames = [c.args[0] for c in dev.send_feature.call_args_list][4:]
        # light.., MACRO, KEYMAP, EXEC — the cached macro frame is re-sent
        self.assertEqual(len(frames), 8)
        self.assertEqual(frames[-3][:3], bytes.fromhex("0605dc"))
        self.assertEqual(bytes(frames[-2][616:620]), bytes((0x10, 0x00, 0x01, 0x00)))

    @patch("time.sleep")
    def test_read_lighting_restores_headers(self, sleep):
        from fizzctl.hid import read_lighting

        dev = Mock(debug=False)
        dev.get_feature.return_value = b"\x00\x00\x00\x00\x00" + \
            bytes.fromhex("deadbeef") + bytes(1023)
        frames = read_lighting(dev)
        self.assertEqual(len(frames), 4)
        self.assertTrue(all(len(f) == 1032 for f in frames))
        self.assertEqual([f[:5] for f in frames], [
            bytes.fromhex("0608b80040"), bytes.fromhex("0609bc0040"),
            bytes.fromhex("0609c00040"), bytes.fromhex("0603b60000"),
        ])
        # payload beyond the header is echoed verbatim
        self.assertEqual(frames[0][5:9], bytes.fromhex("deadbeef"))
        self.assertEqual(dev.send_feature.call_count, 4)

    @patch("time.sleep")
    @patch("fizzctl.cli.open_device")
    def test_cli_keymap_keeps_lighting(self, open_device, sleep):
        dev = Mock(debug=False)
        dev.get_feature.return_value = b"\x00\x00\x00\x00\x00" + \
            bytes.fromhex("deadbeef") + bytes(1023)
        open_device.return_value = dev
        parser = cli._build_parser(False)

        rc = cli.cmd_keymap(parser.parse_args(["keymap", "cfgs/cfg_final.ini"]))
        self.assertEqual(rc, 0)
        dev.close.assert_called_once()
        frames = [c.args[0] for c in dev.send_feature.call_args_list][4:]
        self.assertEqual([f[:3] for f in frames], [
            bytes.fromhex("050581"), bytes.fromhex("0583b6"),
            bytes.fromhex("0608b8"), bytes.fromhex("0609bc"),
            bytes.fromhex("0609c0"), bytes.fromhex("0604d4"),
            bytes.fromhex("0603b6"),
        ])
        # MODE frame sent carries the device's live payload, not the stock one
        self.assertEqual(frames[2][5:9], bytes.fromhex("deadbeef"))

    @patch("time.sleep")
    @patch("fizzctl.cli.open_device")
    def test_cli_keymap_falls_back_when_read_fails(self, open_device, sleep):
        dev = Mock(debug=False)
        dev.get_feature.side_effect = OSError("read error")
        open_device.return_value = dev
        parser = cli._build_parser(False)

        rc = cli.cmd_keymap(parser.parse_args(["keymap", "cfgs/cfg_final.ini"]))
        self.assertEqual(rc, 0)
        frames = [c.args[0] for c in dev.send_feature.call_args_list][1:]
        self.assertEqual(frames[2], blobs.CONST_MODE)   # stock fallback


if __name__ == "__main__":
    unittest.main()
