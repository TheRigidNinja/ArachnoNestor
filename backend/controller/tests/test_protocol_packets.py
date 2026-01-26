import unittest

from protocol.crc8 import crc8
from protocol.framing import build_packet, validate_response
from protocol.evb_packets import PREAMBLE, PING


class TestProtocolPackets(unittest.TestCase):
    def test_build_and_validate(self):
        payload = b""
        pkt = build_packet(PING, payload)
        self.assertEqual(pkt[0], PREAMBLE)
        header = pkt[:3]
        payload_out = pkt[3:-1]
        crc_byte = pkt[-1]
        validate_response(header, payload_out, crc_byte)

    def test_crc8_known(self):
        data = bytes([PREAMBLE, PING, 0x00])
        val = crc8(data)
        self.assertIsInstance(val, int)


if __name__ == "__main__":
    unittest.main()
