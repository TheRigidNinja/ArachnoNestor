import threading
import time
import unittest

from motor.motion_controller import DIRECTION_MAP, HALL_THRESHOLD, STALE_TIMEOUT, WINCH_IDS, MotionController
from motor.safety import SafetyMonitor


class DummyMotor:
    def write_rpm(self, rpm: int) -> None:
        self.last_rpm = rpm

    def start(self, direction: str) -> None:
        self.last_dir = direction

    def stop(self) -> None:
        self.stopped = True


class TestMotionController(MotionController):
    def __init__(self, halls, setup_active):
        self._lock = threading.Lock()
        self.mode = "SETUP"
        self.fault = None
        self.last_halls = halls
        self.last_update = time.time()
        self.last_power = {w: {"bus_mv": 0, "current_ma": 0, "power_mw": 0} for w in WINCH_IDS}
        self.last_bundle = {w: {} for w in WINCH_IDS}
        self.last_imu = None
        self.motor = DummyMotor()
        self._job_thread = None
        self._motor_state = {w: {"running": False, "rpm": 0, "dir": None} for w in WINCH_IDS}
        self._safety = SafetyMonitor(hall_threshold=HALL_THRESHOLD, stale_timeout_s=STALE_TIMEOUT)
        self._setup_hall_active = setup_active


class TestSetupHall(unittest.TestCase):
    def test_setup_hall_below_threshold_does_not_fault(self):
        halls = {w: HALL_THRESHOLD - 1 for w in WINCH_IDS}
        mc = TestMotionController(halls=halls, setup_active=True)
        mc._command_motors(DIRECTION_MAP["up"], rpm=200)
        self.assertIsNone(mc.fault)
        self.assertNotEqual(mc.mode, "FAULT")

    def test_normal_hall_below_threshold_faults(self):
        halls = {w: HALL_THRESHOLD - 1 for w in WINCH_IDS}
        mc = TestMotionController(halls=halls, setup_active=False)
        mc._command_motors(DIRECTION_MAP["up"], rpm=200)
        self.assertIsNotNone(mc.fault)
        self.assertEqual(mc.mode, "FAULT")


if __name__ == "__main__":
    unittest.main()
