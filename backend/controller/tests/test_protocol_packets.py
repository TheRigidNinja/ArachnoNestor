import unittest
import struct

from protocol.crc8 import crc8
from protocol.framing import build_packet, validate_response
from protocol.evb_packets import (
    ARM_ENCODER_TARGET,
    ARM_TENSION_TRIGGER,
    EXPECTED_LENGTHS,
    PREAMBLE,
    PING,
    SAVE_ENCODERS,
)
from tcp import evb


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

    def test_target_status_payload_layout(self):
        payload = struct.pack("<BBBBiiiii", 2, 1, 1, 0, -10, 800, 123, 113, 0)
        self.assertEqual(len(payload), EXPECTED_LENGTHS[ARM_ENCODER_TARGET])
        status = evb.parse_encoder_target_status(payload)
        self.assertEqual(status["winch"], 2)
        self.assertEqual(status["start_count"], -10)
        self.assertEqual(status["target_delta"], 800)
        self.assertEqual(status["current_delta"], 123)

    def test_tension_status_payload_layout(self):
        payload = struct.pack("<BBBBbBHHHHiiH", 4, 1, 1, 1, -1, 0, 1100, 900, 1150, 1150, 5000, 5050, 0)
        self.assertEqual(len(payload), EXPECTED_LENGTHS[ARM_TENSION_TRIGGER])
        status = evb.parse_tension_trigger_status(payload)
        self.assertEqual(status["winch"], 4)
        self.assertEqual(status["direction"], -1)
        self.assertEqual(status["threshold_raw"], 1100)
        self.assertEqual(status["hit_raw"], 1150)

    def test_save_encoders_packet_type(self):
        pkt = build_packet(SAVE_ENCODERS, b"")
        self.assertEqual(pkt[1], SAVE_ENCODERS)


if __name__ == "__main__":
    unittest.main()
