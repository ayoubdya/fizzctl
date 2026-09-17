import hashlib
import unittest
from unittest.mock import Mock, call, patch

from fizzctl import blobs, cli
from fizzctl.effects import encode_firmware_effect
from fizzctl.hid import send_burst
from fizzctl.protocol import base_frames


class RegressionTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
