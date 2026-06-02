import io
import threading
import time
import unittest
from contextlib import redirect_stdout

from motor.motion_controller import DIRECTION_MAP, HALL_THRESHOLD, STALE_TIMEOUT, WINCH_IDS, MotionController
from motor.safety import SafetyMonitor


class DummyMotor:
    def __init__(self):
        self.started = False
        self.stopped = False
        self.events = []
        self.starts = []
        self.stops = []
        self.stop_calls = []
        self.rpm_writes = []

    def write_rpm(self, rpm: int, motor_id: int | None = None, wait_response: bool = True) -> None:
        self.last_rpm = rpm
        self.last_motor_id = motor_id
        self.last_wait_response = wait_response
        self.rpm_writes.append((motor_id, rpm, wait_response))
        self.events.append(("rpm", motor_id, rpm, wait_response))
        return b"ok"

    def start(self, direction: str, motor_id: int | None = None, wait_response: bool = True) -> None:
        self.started = True
        self.last_dir = direction
        self.last_motor_id = motor_id
        self.last_wait_response = wait_response
        self.starts.append((motor_id, direction))
        self.events.append(("start", motor_id, direction, wait_response))
        return b"ok"

    def stop(self, motor_id: int | None = None, wait_response: bool = False, brake: bool = False) -> None:
        self.stopped = True
        self.last_motor_id = motor_id
        self.last_wait_response = wait_response
        self.last_brake = brake
        self.stops.append(motor_id)
        self.stop_calls.append((motor_id, wait_response, brake))
        self.events.append(("stop", motor_id, wait_response, brake))
        return b"ok"


class TestMotionController(MotionController):
    def __init__(self, halls, setup_active):
        self._lock = threading.Lock()
        self._command_lock = threading.RLock()
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
        self.all_winch_wait_response_debug = True


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

    def test_stale_sensor_fault_brakes_all(self):
        halls = {w: HALL_THRESHOLD + 1 for w in WINCH_IDS}
        mc = TestMotionController(halls=halls, setup_active=False)
        mc.last_update = time.time() - STALE_TIMEOUT - 1.0
        mc._command_motors(DIRECTION_MAP["up"], rpm=200)
        self.assertEqual(mc.motor.stop_calls, [
            (1, False, True),
            (2, False, True),
            (3, False, True),
            (4, False, True),
            (1, False, True),
            (2, False, True),
            (3, False, True),
            (4, False, True),
        ])
        self.assertEqual(mc.mode, "FAULT")

    def test_hall_job_safety_fault_brakes_without_natural_stop(self):
        halls = {w: HALL_THRESHOLD + 1 for w in WINCH_IDS}
        mc = TestMotionController(halls=halls, setup_active=True)
        mc.last_update = time.time() - STALE_TIMEOUT - 1.0
        mc._run_hall_job(DIRECTION_MAP["up"], rpm=200, max_seconds=0.0, label="test")
        self.assertEqual(mc.motor.stop_calls, [
            (1, False, True),
            (2, False, True),
            (3, False, True),
            (4, False, True),
            (1, False, True),
            (2, False, True),
            (3, False, True),
            (4, False, True),
        ])
        self.assertEqual(mc.mode, "FAULT")

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
        mc.manual_winch_action("all", "reverse", rpm=200, allow_hall_below=True, wait_response=True)
        self.assertEqual(mc.motor.starts, [(1, "R"), (2, "R"), (3, "R"), (4, "R")])
        self.assertEqual(mc.motor.events, [
            ("rpm", 1, 200, True),
            ("start", 1, "R", True),
            ("rpm", 2, 200, True),
            ("start", 2, "R", True),
            ("rpm", 3, 200, True),
            ("start", 3, "R", True),
            ("rpm", 4, 200, True),
            ("start", 4, "R", True),
        ])
        for winch_id in WINCH_IDS:
            self.assertTrue(mc._motor_state[winch_id]["running"])

    def test_manual_winch_action_all_refresh_reasserts_start(self):
        halls = {w: HALL_THRESHOLD - 1 for w in WINCH_IDS}
        mc = TestMotionController(halls=halls, setup_active=False)
        mc.mode = "SETUP"
        mc.manual_winch_action("all", "reverse", rpm=200, allow_hall_below=True, force=True)
        mc.motor.events.clear()
        mc.motor.starts.clear()
        mc.manual_winch_action("all", "reverse", rpm=200, allow_hall_below=True, force=False)
        self.assertEqual(mc.motor.events, [
            ("rpm", 1, 200, True),
            ("start", 1, "R", True),
            ("rpm", 2, 200, True),
            ("start", 2, "R", True),
            ("rpm", 3, 200, True),
            ("start", 3, "R", True),
            ("rpm", 4, 200, True),
            ("start", 4, "R", True),
        ])
        self.assertEqual(mc.motor.starts, [(1, "R"), (2, "R"), (3, "R"), (4, "R")])

    def test_manual_winch_action_all_ack_logs_failures(self):
        halls = {w: HALL_THRESHOLD - 1 for w in WINCH_IDS}
        mc = TestMotionController(halls=halls, setup_active=False)
        mc.mode = "SETUP"

        def write_rpm(rpm: int, motor_id: int | None = None, wait_response: bool = True):
            mc.motor.events.append(("rpm", motor_id, rpm, wait_response))
            return None if motor_id == 2 else b"ok"

        def start(direction: str, motor_id: int | None = None, wait_response: bool = True):
            mc.motor.events.append(("start", motor_id, direction, wait_response))
            return None if motor_id == 3 else b"ok"

        mc.motor.write_rpm = write_rpm
        mc.motor.start = start
        output = io.StringIO()
        with redirect_stdout(output):
            mc.manual_winch_action("all", "forward", rpm=200, allow_hall_below=True)
        self.assertIn("ALL MOVE FAILED: motor 2 no ACK on RPM", output.getvalue())
        self.assertIn("ALL MOVE FAILED: motor 3 no ACK on START", output.getvalue())

    def test_setup_all_run_test_commands_each_motor_with_ack_then_brakes(self):
        halls = {w: HALL_THRESHOLD - 1 for w in WINCH_IDS}
        mc = TestMotionController(halls=halls, setup_active=False)
        mc.mode = "SETUP"
        label = mc.setup_all_run_test(rpm=500, seconds=0, direction="forward")
        self.assertEqual(label, "setup_all_run_test")
        self.assertEqual(mc.motor.events, [
            ("rpm", 1, 500, True),
            ("start", 1, "F", True),
            ("rpm", 2, 500, True),
            ("start", 2, "F", True),
            ("rpm", 3, 500, True),
            ("start", 3, "F", True),
            ("rpm", 4, 500, True),
            ("start", 4, "F", True),
            ("stop", 1, False, True),
            ("stop", 2, False, True),
            ("stop", 3, False, True),
            ("stop", 4, False, True),
        ])

    def test_setup_all_run_test_group_targets(self):
        groups = {
            "1+2": [1, 2],
            "3+4": [3, 4],
            "1+3": [1, 3],
            "2+4": [2, 4],
        }
        for group, motors in groups.items():
            with self.subTest(group=group):
                halls = {w: HALL_THRESHOLD - 1 for w in WINCH_IDS}
                mc = TestMotionController(halls=halls, setup_active=False)
                mc.mode = "SETUP"
                mc.setup_all_run_test(rpm=500, seconds=0, direction="forward", group=group)
                self.assertEqual(mc.motor.starts, [(motor_id, "F") for motor_id in motors])

    def test_setup_all_run_test_blocked_outside_setup(self):
        halls = {w: HALL_THRESHOLD + 1 for w in WINCH_IDS}
        mc = TestMotionController(halls=halls, setup_active=False)
        mc.mode = "TEST"
        with self.assertRaises(RuntimeError):
            mc.setup_all_run_test(rpm=500, seconds=0, direction="forward")

    def test_stop_all_forces_stop_even_if_state_is_wrong(self):
        halls = {w: HALL_THRESHOLD + 1 for w in WINCH_IDS}
        mc = TestMotionController(halls=halls, setup_active=False)
        mc.stop_all("operator stop")
        self.assertEqual(mc.motor.stops, [1, 2, 3, 4])
        self.assertFalse(mc.motor.last_wait_response)
        self.assertFalse(mc.motor.last_brake)

    def test_stop_all_as_fault_uses_repeated_brake(self):
        halls = {w: HALL_THRESHOLD + 1 for w in WINCH_IDS}
        mc = TestMotionController(halls=halls, setup_active=False)
        mc.stop_all("fault stop", as_fault=True)
        self.assertEqual(mc.motor.stop_calls, [
            (1, False, True),
            (2, False, True),
            (3, False, True),
            (4, False, True),
            (1, False, True),
            (2, False, True),
            (3, False, True),
            (4, False, True),
        ])
        self.assertEqual(mc.mode, "FAULT")
        self.assertEqual(mc.fault, "fault stop")

    def test_emergency_stop_uses_brake_without_waiting(self):
        halls = {w: HALL_THRESHOLD + 1 for w in WINCH_IDS}
        mc = TestMotionController(halls=halls, setup_active=False)
        mc.emergency_stop("test emergency")
        self.assertEqual(mc.motor.stop_calls, [
            (1, False, True),
            (2, False, True),
            (3, False, True),
            (4, False, True),
            (1, False, True),
            (2, False, True),
            (3, False, True),
            (4, False, True),
        ])
        self.assertEqual(mc.mode, "FAULT")

    def test_safe_brake_all_repeats_brake_and_faults(self):
        halls = {w: HALL_THRESHOLD + 1 for w in WINCH_IDS}
        mc = TestMotionController(halls=halls, setup_active=False)
        mc.safe_brake_all("test fault")
        self.assertEqual(mc.motor.stop_calls, [
            (1, False, True),
            (2, False, True),
            (3, False, True),
            (4, False, True),
            (1, False, True),
            (2, False, True),
            (3, False, True),
            (4, False, True),
        ])
        self.assertEqual(mc.mode, "FAULT")
        self.assertEqual(mc.fault, "test fault")

    def test_safe_brake_all_replaces_existing_fault_reason(self):
        halls = {w: HALL_THRESHOLD + 1 for w in WINCH_IDS}
        mc = TestMotionController(halls=halls, setup_active=False)
        mc.fault = "old fault"
        mc.safe_brake_all("new fault")
        self.assertEqual(mc.fault, "new fault")

    def test_selected_winch_stop_brakes_target_only_with_ack_debug(self):
        halls = {w: HALL_THRESHOLD + 1 for w in WINCH_IDS}
        mc = TestMotionController(halls=halls, setup_active=False)
        mc.selected_winch_stop(3)
        self.assertEqual(mc.motor.stop_calls, [(3, True, True)])

    def test_selected_winch_stop_all_brakes_all_only(self):
        halls = {w: HALL_THRESHOLD + 1 for w in WINCH_IDS}
        mc = TestMotionController(halls=halls, setup_active=False)
        mc.selected_winch_stop("all")
        self.assertEqual(mc.motor.stop_calls, [
            (1, False, True),
            (2, False, True),
            (3, False, True),
            (4, False, True),
            (1, False, True),
            (2, False, True),
            (3, False, True),
            (4, False, True),
        ])

    def test_selected_winch_stop_logs_no_ack(self):
        halls = {w: HALL_THRESHOLD + 1 for w in WINCH_IDS}
        mc = TestMotionController(halls=halls, setup_active=False)

        def stop_no_ack(motor_id: int | None = None, wait_response: bool = False, brake: bool = False):
            mc.motor.stop_calls.append((motor_id, wait_response, brake))
            return None

        mc.motor.stop = stop_no_ack
        output = io.StringIO()
        with redirect_stdout(output):
            mc.selected_winch_stop(2)
        self.assertEqual(mc.motor.stop_calls, [(2, True, True)])
        self.assertIn("STOP FAILED: no ACK from motor 2", output.getvalue())

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

    def test_manual_winch_action_still_starts_if_rpm_ack_fails(self):
        halls = {w: HALL_THRESHOLD + 1 for w in WINCH_IDS}
        mc = TestMotionController(halls=halls, setup_active=False)
        mc.mode = "SETUP"
        mc.motor.write_rpm = lambda *args, **kwargs: None
        output = io.StringIO()
        with redirect_stdout(output):
            mc.manual_winch_action(1, "forward", rpm=200, allow_hall_below=True, force=True, wait_response=True)
        self.assertEqual(mc.motor.starts, [(1, "F")])
        self.assertIn("RPM FAILED: no ACK from motor 1", output.getvalue())

    def test_manual_winch_action_logs_start_ack_failure(self):
        halls = {w: HALL_THRESHOLD + 1 for w in WINCH_IDS}
        mc = TestMotionController(halls=halls, setup_active=False)
        mc.mode = "SETUP"

        def start_no_ack(direction: str, motor_id: int | None = None, wait_response: bool = True):
            mc.motor.starts.append((motor_id, direction))
            return None

        mc.motor.start = start_no_ack
        output = io.StringIO()
        with redirect_stdout(output):
            mc.manual_winch_action(1, "forward", rpm=200, allow_hall_below=True, force=True, wait_response=True)
        self.assertEqual(mc.motor.starts, [(1, "F")])
        self.assertIn("START FAILED: no ACK from motor 1", output.getvalue())

if __name__ == "__main__":
    unittest.main()
