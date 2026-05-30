import threading
import time
import unittest

from motor.motion_controller import DIRECTION_MAP, HALL_THRESHOLD, STALE_TIMEOUT, WINCH_IDS, MotionController
from motor.safety import SafetyMonitor


class DummyMotor:
    def __init__(self):
        self.started = False
        self.stopped = False
        self.starts = []
        self.stops = []

    def write_rpm(self, rpm: int, motor_id: int | None = None, wait_response: bool = True) -> None:
        self.last_rpm = rpm
        self.last_motor_id = motor_id
        self.last_wait_response = wait_response
        return b"ok"

    def start(self, direction: str, motor_id: int | None = None, wait_response: bool = True) -> None:
        self.started = True
        self.last_dir = direction
        self.last_motor_id = motor_id
        self.last_wait_response = wait_response
        self.starts.append((motor_id, direction))
        return b"ok"

    def stop(self, motor_id: int | None = None, wait_response: bool = False) -> None:
        self.stopped = True
        self.last_motor_id = motor_id
        self.last_wait_response = wait_response
        self.stops.append(motor_id)
        return b"ok"


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
        self.motor_available = True
        self.motor_error = None
        self.motor = DummyMotor()
        self._job_thread = None
        self._motor_state = {w: {"running": False, "rpm": 0, "dir": None} for w in WINCH_IDS}
        self._safety = SafetyMonitor(hall_threshold=HALL_THRESHOLD, stale_timeout_s=STALE_TIMEOUT)
        self._setup_hall_active = setup_active
        self._allow_hall_below = False


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
        self.assertIsNone(mc.fault)
        self.assertNotEqual(mc.mode, "FAULT")

    def test_directional_tests_blocked_when_hall_low(self):
        halls = {w: HALL_THRESHOLD - 1 for w in WINCH_IDS}
        mc = TestMotionController(halls=halls, setup_active=False)
        mc.mode = "TEST"
        with self.assertRaises(RuntimeError):
            mc.test_direction("forward", rpm=200, seconds=1.0)

    def test_allow_hall_below_does_not_fault(self):
        halls = {w: HALL_THRESHOLD - 1 for w in WINCH_IDS}
        mc = TestMotionController(halls=halls, setup_active=False)
        mc._allow_hall_below = True
        mc._command_motors(DIRECTION_MAP["up"], rpm=200)
        self.assertIsNone(mc.fault)

    def test_idle_mode_does_not_command_motors(self):
        halls = {w: HALL_THRESHOLD + 1 for w in WINCH_IDS}
        mc = TestMotionController(halls=halls, setup_active=False)
        mc.mode = "IDLE"
        mc._command_motors(DIRECTION_MAP["up"], rpm=200)
        self.assertFalse(mc.motor.started)

    def test_manual_winch_action_targets_single_winch_with_hall_bypass(self):
        halls = {w: HALL_THRESHOLD - 1 for w in WINCH_IDS}
        mc = TestMotionController(halls=halls, setup_active=False)
        mc.mode = "SETUP"
        mc.manual_winch_action(3, "forward", rpm=200, allow_hall_below=True)
        self.assertEqual(mc.motor.starts, [(3, "F")])
        self.assertTrue(mc._motor_state[3]["running"])
        self.assertFalse(mc._motor_state[1]["running"])
        self.assertFalse(mc._motor_state[2]["running"])
        self.assertFalse(mc._motor_state[4]["running"])

    def test_manual_winch_action_blocked_outside_setup(self):
        halls = {w: HALL_THRESHOLD + 1 for w in WINCH_IDS}
        mc = TestMotionController(halls=halls, setup_active=False)
        mc.mode = "TEST"
        with self.assertRaises(RuntimeError):
            mc.manual_winch_action(3, "forward", rpm=200, allow_hall_below=True)

    def test_manual_winch_action_all_targets_all_winches(self):
        halls = {w: HALL_THRESHOLD - 1 for w in WINCH_IDS}
        mc = TestMotionController(halls=halls, setup_active=False)
        mc.mode = "SETUP"
        mc.manual_winch_action("all", "reverse", rpm=200, allow_hall_below=True)
        self.assertEqual(mc.motor.starts, [(1, "R"), (2, "R"), (3, "R"), (4, "R")])
        for winch_id in WINCH_IDS:
            self.assertTrue(mc._motor_state[winch_id]["running"])

    def test_stop_all_forces_stop_even_if_state_is_wrong(self):
        halls = {w: HALL_THRESHOLD + 1 for w in WINCH_IDS}
        mc = TestMotionController(halls=halls, setup_active=False)
        mc.stop_all("operator stop")
        self.assertEqual(mc.motor.stops, [1, 2, 3, 4])
        self.assertTrue(mc.motor.last_wait_response)

    def test_manual_winch_action_force_rewrites_all_commands(self):
        halls = {w: HALL_THRESHOLD + 1 for w in WINCH_IDS}
        mc = TestMotionController(halls=halls, setup_active=False)
        mc.mode = "SETUP"
        for winch_id in WINCH_IDS:
            mc._motor_state[winch_id] = {"running": True, "rpm": 200, "dir": "R"}
        mc.manual_winch_action("all", "reverse", rpm=200, allow_hall_below=True, force=True)
        self.assertEqual(mc.motor.starts, [(1, "R"), (2, "R"), (3, "R"), (4, "R")])

    def test_manual_winch_action_no_ack_does_not_mark_running(self):
        halls = {w: HALL_THRESHOLD + 1 for w in WINCH_IDS}
        mc = TestMotionController(halls=halls, setup_active=False)
        mc.mode = "SETUP"
        mc.motor.write_rpm = lambda *args, **kwargs: None
        mc.motor.start = lambda *args, **kwargs: None
        mc.manual_winch_action(1, "forward", rpm=200, allow_hall_below=True, force=True, wait_response=True)
        self.assertFalse(mc._motor_state[1]["running"])
        self.assertEqual(mc._motor_state[1]["rpm"], 0)
        self.assertIsNone(mc._motor_state[1]["dir"])

if __name__ == "__main__":
    unittest.main()
