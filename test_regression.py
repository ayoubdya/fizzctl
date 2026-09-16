import hashlib
import unittest
from unittest.mock import Mock, call, patch

from fizzctl import blobs, cli
from fizzctl.hid import send_burst


class RegressionTests(unittest.TestCase):
    def test_packet_bytes(self):
        names = ("CONST_MODE", "CONST_CANVAS", "CONST_ROUTING", "CONST_EXEC", "RGB_SEC", "RGB_EXEC")
        data = b"".join(getattr(blobs, name) for name in names) + b"".join(blobs.FW_TEMPLATE)
        self.assertEqual(hashlib.sha256(data).hexdigest(), "d047671cf51c71cdc330ddc5a462a52cc43170254d5ff1739387b52ef0999c23")

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
