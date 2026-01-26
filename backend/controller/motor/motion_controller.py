#!/usr/bin/env python3
"""
Motion controller (module-friendly).

This module owns all movement decisions and safety gating.
Other modules request actions; this module decides if they run.
"""

from __future__ import annotations

import threading
import time
from typing import Dict, List, Optional

from config.settings import load_config
from drivers.bld510b import MotorBus, SERIAL_PORT, BAUD_RATE
from drivers.evb_driver import EVBDriver
from logutil.logger import get_logger
from motor.profiles import PID, DEFAULT_BALANCE_PID
from motor.safety import SafetyMonitor


# ---- constants ----
CONFIG = load_config()
WINCH_IDS: List[int] = CONFIG["motion"]["winch_ids"]
HALL_THRESHOLD = CONFIG["motion"]["hall_threshold"]
POLL_INTERVAL = CONFIG["motion"]["poll_interval"]
STALE_TIMEOUT = CONFIG["motion"]["stale_timeout"]

log = get_logger("motor.motion_controller")

# Motor directions mapping for directional tests
DIRECTION_MAP = {
    "forward": [+1, +1, -1, -1],
    "back":    [-1, -1, +1, +1],
    "left":    [-1, +1, -1, +1],
    "right":   [+1, -1, +1, -1],
    "up":      [+1, +1, +1, +1],
    "down":    [-1, -1, -1, -1],
}


class MotionController:
    def __init__(self,
                 host: str = CONFIG["evb"]["host"],
                 port: int = CONFIG["evb"]["port"],
                 timeout: float = CONFIG["evb"]["timeout"],
                 serial_port: str | None = CONFIG["motion"]["serial_port"],
                 baud_rate: int | None = CONFIG["motion"]["baud_rate"]):
        self.host = host
        self.port = port
        self.timeout = timeout

        # Shared state guarded by lock
        self._lock = threading.Lock()
        self.mode: str = "IDLE"
        self.fault: Optional[str] = None
        self.last_halls: Dict[int, int] = {w: 0 for w in WINCH_IDS}
        self.last_power: Dict[int, Dict[str, int]] = {w: {"bus_mv": 0, "current_ma": 0, "power_mw": 0} for w in WINCH_IDS}
        self.last_bundle: Dict[int, Dict[str, int]] = {w: {} for w in WINCH_IDS}
        self.last_imu: Optional[Dict[str, float]] = None
        self.last_update: Optional[float] = None

        # Motor driver owns serial access
        self.motor = MotorBus(
            port=serial_port or SERIAL_PORT,
            baudrate=baud_rate or BAUD_RATE,
        )

        # Background poller
        self._stop_event = threading.Event()
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()

        # Currently running motion job
        self._job_thread: Optional[threading.Thread] = None

        # Track last command per motor to avoid spamming Modbus
        self._motor_state = {w: {"running": False, "rpm": 0, "dir": None} for w in WINCH_IDS}
        self._safety = SafetyMonitor(hall_threshold=HALL_THRESHOLD, stale_timeout_s=STALE_TIMEOUT)

    # ------------- public API (used by web server or main) -------------
    def get_status(self) -> dict:
        with self._lock:
            halls = {str(k): v for k, v in self.last_halls.items()}
            power = {str(k): v.copy() for k, v in self.last_power.items()}
            bundles = {str(k): v.copy() for k, v in self.last_bundle.items()}
            imu = self.last_imu.copy() if self.last_imu else None
            return {
                "mode": self.mode,
                "fault": self.fault,
                "halls": halls,
                "power": power,
                "bundles": bundles,
                "imu": imu,
                "threshold": HALL_THRESHOLD,
                "last_update": self.last_update,
                "job_active": self._job_thread is not None and self._job_thread.is_alive(),
            }

    def set_mode(self, mode: str) -> None:
        mode = mode.upper()
        if mode not in {"IDLE", "SETUP", "TEST", "FAULT"}:
            raise ValueError("invalid mode")
        with self._lock:
            # If already faulted, only allow clear_fault to exit
            if self.fault and mode != "FAULT":
                raise RuntimeError("in FAULT; clear_fault first")
            # Stop motors before switching
            self._stop_motors_locked("mode change")
            self.mode = mode

    def clear_fault(self) -> None:
        with self._lock:
            self.fault = None
            self.mode = "IDLE"
            self._stop_motors_locked("fault cleared")

    def stop_all(self, reason: str = "user stop", as_fault: bool = False) -> None:
        """Stop motors. If as_fault=True, enter FAULT mode and record reason."""
        with self._lock:
            self._stop_motors_locked(reason)
            if as_fault:
                self.fault = reason if self.fault is None else self.fault
                self.mode = "FAULT"

    def emergency_stop(self, reason: str = "emergency stop") -> None:
        """Force-stop and enter FAULT regardless of current state."""
        self.stop_all(reason=reason, as_fault=True)

    def setup_jog(self, rpm: int = 200, seconds: float = 1.0) -> str:
        with self._lock:
            self._ensure_ready("SETUP")
        return self._start_job(targets=DIRECTION_MAP["up"], rpm=rpm, seconds=seconds, label="setup_jog")

    def test_up(self, rpm: int = 350, seconds: float = 10.0) -> str:
        with self._lock:
            self._ensure_ready("TEST")
        return self._start_job(targets=DIRECTION_MAP["up"], rpm=rpm, seconds=seconds, label="test_up")

    def test_direction(self, name: str, rpm: int = 350, seconds: float = 6.0) -> str:
        name = name.lower()
        if name not in DIRECTION_MAP:
            raise ValueError("invalid direction")
        with self._lock:
            self._ensure_ready("TEST")
        return self._start_job(targets=DIRECTION_MAP[name], rpm=rpm, seconds=seconds, label=f"dir_{name}")

    def run_balance_loop(
        self,
        base_rpm: float = 1000.0,
        sample_hz: float = 50.0,
        min_interval: float = 0.02,
        max_interval: float = 0.2,
        backoff: float = 1.5,
        recover: float = 0.9,
        no_motors: bool = False,
        host: Optional[str] = None,
        port: Optional[int] = None,
        timeout: Optional[float] = None,
    ) -> int:
        """Blocking IMU balance loop; uses motion controller for movement commands."""
        pid_roll = PID(**DEFAULT_BALANCE_PID)
        interval = max(min_interval, 1.0 / sample_hz if sample_hz > 0 else min_interval)
        host = host or self.host
        port = port or self.port
        timeout = timeout if timeout is not None else self.timeout

        try:
            last = time.time()
            log.info("Starting balance loop (IMU from poller)… Ctrl-C to exit")
            while True:
                with self._lock:
                    if self.fault:
                        log.warning(f"FAULT: {self.fault}; stopping balance loop")
                        break
                    imu = self.last_imu
                    updated = self.last_update

                if imu is None or updated is None:
                    time.sleep(0.05)
                    continue

                if time.time() - updated > max_interval:
                    time.sleep(0.05)
                    continue

                loop_start = time.time()
                dt = loop_start - last
                last = loop_start

                roll = imu["roll"]
                correction = pid_roll.update(roll, dt)
                rpm_target = base_rpm + correction

                if not no_motors:
                    self._command_motors(DIRECTION_MAP["up"], rpm_target)

                log.info(
                    f"roll={roll:+6.2f}° pitch={imu['pitch']:+6.2f} yaw={imu['yaw']:+6.2f} "
                    f"gyro=({imu['gyro'][0]:+.2f},{imu['gyro'][1]:+.2f},{imu['gyro'][2]:+.2f}) "
                    f"corr={correction:+7.1f} → RPM={rpm_target:.0f}"
                )
                interval = max(min_interval, min(max_interval, interval * recover))
                sleep_for = max(0.0, interval - (time.time() - loop_start))
                time.sleep(sleep_for)
        except KeyboardInterrupt:
            log.info("Shutting down…")
        finally:
            if not no_motors:
                try:
                    self._stop_motors("balance loop stop")
                except KeyboardInterrupt:
                    pass
        return 0

    # ------------- internal helpers -------------
    def _ensure_ready(self, required_mode: str) -> None:
        if self.fault:
            raise RuntimeError(f"FAULT: {self.fault}")
        if self.mode != required_mode:
            raise RuntimeError(f"mode must be {required_mode}")
        if self._job_thread is not None and self._job_thread.is_alive():
            raise RuntimeError("another job running")

    def _start_job(self, targets: List[int], rpm: int, seconds: float, label: str) -> str:
        job = threading.Thread(target=self._run_job, args=(targets, rpm, seconds, label), daemon=True)
        self._job_thread = job
        log.info(f"Job start: {label} rpm={rpm} sec={seconds}")
        job.start()
        return label

    def _run_job(self, targets: List[int], rpm: int, seconds: float, label: str):
        try:
            self._command_motors(targets, rpm)
            time.sleep(max(0.0, seconds))
        except Exception as exc:  # safety: any failure → fault
            self.stop_all(f"job error {label}: {exc}", as_fault=True)
            return
        finally:
            self._stop_motors("job finished")
            with self._lock:
                self._job_thread = None

    def _command_motors(self, targets: List[int], rpm: int):
        with self._lock:
            status = self._safety.evaluate(self.last_halls, self.last_update)
            if not status.can_move:
                self.fault = status.reason
                self.mode = "FAULT"
                log.warning(f"Motion blocked: {status.reason}")
                self._stop_motors_locked("safety stop")
                return
        abs_rpm = max(0, int(rpm))
        for motor_id, direction in zip(WINCH_IDS, targets):
            if direction == 0:
                self._stop_motor(motor_id)
                continue
            desired_dir = "F" if direction > 0 else "R"
            state = self._motor_state[motor_id]
            if (not state["running"]) or state["rpm"] != abs_rpm:
                self.motor.write_rpm(abs_rpm)
                state["rpm"] = abs_rpm
            if (not state["running"]) or state["dir"] != desired_dir:
                self.motor.start(desired_dir)
                state["dir"] = desired_dir
            state["running"] = True

    def _stop_motor(self, motor_id: int):
        state = self._motor_state[motor_id]

        print(  f"Stopping motor {self._motor_state[motor_id]}...")  # Debug print
        if not state["running"]:
            return
        try:
            self.motor.stop()
        except Exception:
            pass
        state["running"] = False

    def _stop_motors(self, reason: str = ""):
        with self._lock:
            self._stop_motors_locked(reason)

    def _stop_motors_locked(self, reason: str = ""):
        for mid in WINCH_IDS:
            self._stop_motor(mid)

    def _can_move_locked(self) -> bool:
        # caller must hold lock
        status = self._safety.evaluate(self.last_halls, self.last_update)
        return status.can_move

    # ------------- polling & safety -------------
    def _poll_loop(self):
        while not self._stop_event.is_set():
            try:
                with EVBDriver(self.host, self.port, self.timeout) as evb:
                    while not self._stop_event.is_set():
                        cycle_start = time.time()
                        halls = {}
                        power = {}
                        bundles = {}
                        try:
                            for w in WINCH_IDS:
                                bundle = evb.bundle(w)
                                halls[w] = bundle.hall_raw
                                power[w] = {
                                    "bus_mv": bundle.bus_mv,
                                    "current_ma": bundle.current_ma,
                                    "power_mw": bundle.power_mw,
                                }
                                bundles[w] = bundle.__dict__
                            try:
                                imu = evb.imu()
                            except Exception:
                                imu = None
                        except Exception as exc:
                            self.stop_all(f"EVB error: {exc}", as_fault=True)
                            break

                        now = time.time()
                        with self._lock:
                            self.last_halls.update(halls)
                            self.last_power.update(power)
                            self.last_bundle.update(bundles)
                            if imu:
                                self.last_imu = {
                                    "gyro": imu.gyro,
                                    "accel": imu.accel,
                                    "temp_c": imu.temp_c,
                                    "pitch": imu.pitch,
                                    "roll": imu.roll,
                                    "yaw": imu.yaw,
                                }
                            self.last_update = now
                            status = self._safety.evaluate(self.last_halls, self.last_update)
                            if not status.can_move:
                                self.fault = status.reason
                                self.mode = "FAULT"
                                self._stop_motors_locked("safety stop")

                        elapsed = time.time() - cycle_start
                        sleep_for = max(0.0, POLL_INTERVAL - elapsed)
                        time.sleep(sleep_for)
            except Exception as exc:
                self.stop_all(f"EVB connection failure: {exc}", as_fault=True)
                time.sleep(0.2)

    def shutdown(self):
        self._stop_event.set()
        self._poll_thread.join(timeout=1.0)
        self._stop_motors("shutdown")
        try:
            self.motor.close()
        except Exception:
            pass


# singleton helper for the web server or main entrypoint
controller: Optional[MotionController] = None


def get_controller() -> MotionController:
    global controller
    if controller is None:
        controller = MotionController()
    return controller


if __name__ == "__main__":
    mc = get_controller()
    log.info("MotionController running. Press Ctrl-C to exit.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        mc.shutdown()
