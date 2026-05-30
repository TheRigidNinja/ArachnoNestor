import unittest

from drivers import bld510b


class FakeSerial:
    def __init__(self, response=b""):
        self.response = response
        self.frames = []
        self.read_calls = 0
        self.read_sizes = []
        self.flush_calls = 0
        self.reset_calls = 0

    def reset_input_buffer(self):
        self.reset_calls += 1

    def write(self, frame):
        self.frames.append(bytes(frame))

    def flush(self):
        self.flush_calls += 1

    def read(self, size):
        self.read_calls += 1
        self.read_sizes.append(size)
        if not self.response:
            return b""
        return self.response[:size]

    def read_all(self):
        self.read_calls += 1
        return self.response


class TestBLD510BDriver(unittest.TestCase):
    def test_stop_sends_addressed_stop_frame(self):
        ser = FakeSerial()
        bld510b.stop_motor_natural(ser, device_address=2)
        self.assertEqual(ser.read_calls, 0)
        self.assertEqual(ser.frames[0][0], 2)
        self.assertEqual(ser.frames[0][1], 0x06)
        self.assertEqual(ser.frames[0][2:4], b"\x80\x00")
        self.assertEqual(ser.frames[0][4:6], b"\x08\x02")

    def test_braking_stop_sends_addressed_brake_frame(self):
        ser = FakeSerial()
        bld510b.stop_motor_braking(ser, device_address=2)
        self.assertEqual(ser.read_calls, 0)
        self.assertEqual(ser.frames[0][0], 2)
        self.assertEqual(ser.frames[0][1], 0x06)
        self.assertEqual(ser.frames[0][2:4], b"\x80\x00")
        self.assertEqual(ser.frames[0][4:6], b"\x0D\x02")

    def test_write_rpm_is_best_effort_on_no_response(self):
        ser = FakeSerial(response=b"")
        response = bld510b.write_rpm(ser, 250, device_address=2)
        self.assertIsNone(response)
        self.assertEqual(ser.read_calls, 1)
        self.assertEqual(ser.read_sizes, [8])

    def test_write_rpm_can_skip_response_wait(self):
        ser = FakeSerial(response=b"")
        response = bld510b.write_rpm(ser, 250, device_address=2, wait_response=False)
        self.assertEqual(response, b"")
        self.assertEqual(ser.read_calls, 0)

    def test_read_command_uses_expected_length(self):
        ser = FakeSerial(response=b"\x02\x03\x02\xC8\x00\xAB\x84")
        response = bld510b.send_modbus_command(ser, 0x03, 0x8005, count=1, device_address=2)
        self.assertEqual(response, b"\x02\x03\x02\xC8\x00\xAB\x84")
        self.assertEqual(ser.read_sizes, [7])


if __name__ == "__main__":
    unittest.main()
