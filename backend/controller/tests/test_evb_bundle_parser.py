import unittest

from tcp import evb


class DummyClient:
    def send(self, type_byte, payload):
        self.type_byte = type_byte
        self.payload = payload
        bundle_payload = bytes(
            [
                1, 0,
                0, 0, 0, 0,
                0, 0, 0, 0,
                0x57, 0x04,
                0, 0,
                0, 0,
                0, 0,
                1, 0,
                0xFF, 0xFF,
                0xFF, 0xFF,
                0xFF, 0xFF, 0xFF, 0xFF,
                7, 0, 0, 0,
            ]
        )
        return evb.BUNDLE, bundle_payload


class TestEvbBundleParser(unittest.TestCase):
    def test_accepts_current_32_byte_bundle(self):
        result = evb.get_bundle(DummyClient(), 1)

        self.assertEqual(result["winch"], 1)
        self.assertEqual(result["hall_raw"], 1111)
        self.assertEqual(result["bus_mv"], 65535)
        self.assertEqual(result["current_ma"], -1)
        self.assertEqual(result["power_mw"], 4294967295)
        self.assertEqual(result["cache_age_ms"], 7)


if __name__ == "__main__":
    unittest.main()
