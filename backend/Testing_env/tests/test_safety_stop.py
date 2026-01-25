import time
import unittest

from motor.safety import SafetyMonitor


class TestSafetyStop(unittest.TestCase):
    def test_hall_low_trips(self):
        safety = SafetyMonitor(hall_threshold=1500, stale_timeout_s=0.5)
        halls = {1: 1400, 2: 1600, 3: 1600, 4: 1600}
        status = safety.evaluate(halls, time.time())
        self.assertFalse(status.can_move)

    def test_stale_trips(self):
        safety = SafetyMonitor(hall_threshold=1500, stale_timeout_s=0.1)
        halls = {1: 1600, 2: 1600, 3: 1600, 4: 1600}
        status = safety.evaluate(halls, time.time() - 1.0)
        self.assertFalse(status.can_move)


if __name__ == "__main__":
    unittest.main()
