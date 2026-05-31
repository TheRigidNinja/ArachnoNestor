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
from drivers.bld510b import DisabledMotorBus, MotorBus, SERIAL_PORT, BAUD_RATE
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
EVB_BACKOFF_INITIAL = CONFIG["motion"]["evb_backoff_initial"]
EVB_BACKOFF_MAX = CONFIG["motion"]["evb_backoff_max"]
EVB_BACKOFF_FACTOR = CONFIG["motion"]["evb_backoff_factor"]
USE_BUNDLE = CONFIG["motion"].get("use_bundle", True)
USE_POWER = CONFIG["motion"].get("use_power", True)
USE_IMU = CONFIG["motion"].get("use_imu", True)

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

# Setup mode uses a simple forward/reverse axis.
SETUP_DIR_MAP = {
    "forward": DIRECTION_MAP["up"],
    "reverse": DIRECTION_MAP["down"],
}


class MotionController:
    def __init__(self,
                 host: str = CONFIG["evb"]["host"],
                 port: int = CONFIG["evb"]["port"],
                 timeout: float = CONFIG["evb"]["timeout"],
                 serial_port: str | None = CONFIG["motion"]["serial_port"],
                 baud_rate: int | None = CONFIG["motion"]["baud_rate"],
                 no_motors: bool = False):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.motor_available = False
        self.motor_error: Optional[str] = None

        # Shared state guarded by lock
        self._lock = threading.Lock()
        self._command_lock = threading.RLock()
        self.mode: str = "IDLE"
        self.fault: Optional[str] = None
        self.setup_activated: bool = False
        self.last_halls: Dict[int, int] = {w: 0 for w in WINCH_IDS}
        self.last_power: Dict[int, Dict[str, int]] = {w: {"bus_mv": 0, "current_ma": 0, "power_mw": 0} for w in WINCH_IDS}
        self.last_bundle: Dict[int, Dict[str, int]] = {w: {} for w in WINCH_IDS}
        self.last_imu: Optional[Dict[str, float]] = None
        self.last_update: Optional[float] = None

        # Motor driver owns serial access. If unavailable, keep EVB/web online
        # and make every movement command fail closed.
        if no_motors:
            self.motor_error = "started with --no-motors"
            self.motor = DisabledMotorBus(self.motor_error)
        else:
            try:
                self.motor = MotorBus(
                    port=serial_port or SERIAL_PORT,
                    baudrate=baud_rate or BAUD_RATE,
                )
                self.motor_available = True
            except Exception as exc:
                self.motor_error = str(exc)
                log.error(f"Motor RS485 unavailable: {exc}")
                self.motor = DisabledMotorBus(self.motor_error)

        # Background poller
        self._stop_event = threading.Event()
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()

        # Currently running motion job
        self._job_thread: Optional[threading.Thread] = None
        self._setup_hall_active = False
        self._allow_hall_below = False
        self._evb_error_count = 0
        self._evb_error_streak = 0
        self._evb_last_error = None
        self._evb_last_error_ts = None

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
                "setup_activated": self.setup_activated,
                "evb_error_count": self._evb_error_count,
                "evb_error_streak": self._evb_error_streak,
                "evb_last_error": self._evb_last_error,
                "evb_last_error_ts": self._evb_last_error_ts,
                "motor_available": self.motor_available,
                "motor_error": self.motor_error,
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
        with self._command_lock:
            with self._lock:
                # If already faulted, only allow clear_fault to exit
                if self.fault and mode != "FAULT":
                    raise RuntimeError("in FAULT; clear_fault first")
                if mode == "IDLE":
                    self.setup_activated = False
                # Stop motors before switching
                self._stop_motors_locked("mode change", force=True)
                self.mode = mode
                if mode == "SETUP":
                    self.setup_activated = True

    def clear_fault(self) -> None:
        with self._command_lock:
            with self._lock:
                self.fault = None
                self.mode = "IDLE"
                self.setup_activated = False
                self._stop_motors_locked("fault cleared", force=True)

    def stop_all(
        self,
        reason: str = "user stop",
        as_fault: bool = False,
        wait_response: bool = False,
        brake: bool = False,
    ) -> None:
        """Stop motors. If as_fault=True, enter FAULT mode and record reason."""
        with self._command_lock:
            if as_fault and not brake:
                brake = True
            with self._lock:
                self._stop_motors_locked(reason, force=True, wait_response=wait_response, brake=brake)
                self._allow_hall_below = False
                if as_fault:
                    self.fault = reason if self.fault is None else self.fault
                    self.mode = "FAULT"

    def safe_brake_all(self, reason: str) -> None:
        """Repeated braking stop that is safe to call from fault paths."""
        with self._command_lock:
            self._safe_brake_all_under_command_lock(reason)

    def _safe_brake_all_under_command_lock(self, reason: str) -> None:
        self._brake_stop_targets(list(WINCH_IDS), wait_response=False)
        time.sleep(0.1)
        self._brake_stop_targets(list(WINCH_IDS), wait_response=False)
        with self._lock:
            self._allow_hall_below = False
            self.fault = reason if self.fault is None else self.fault
            self.mode = "FAULT"

    def emergency_stop(self, reason: str = "emergency stop") -> None:
        """Force-stop and enter FAULT regardless of current state."""
        self.safe_brake_all(reason)

    def setup_jog(self, rpm: int = 200, seconds: float = 1.0) -> str:
        with self._lock:
            self._ensure_ready("SETUP")
        return self._start_job(targets=DIRECTION_MAP["up"], rpm=rpm, seconds=seconds, label="setup_jog")

    def setup_hall_run(self, rpm: int = 200, seconds: float = 0.0, direction: str = "forward") -> str:
        direction = direction.lower()
        if direction not in SETUP_DIR_MAP:
            raise ValueError("invalid direction")
        
        max_seconds = float(seconds) if seconds and seconds > 0 else None

        with self._lock:
            self._ensure_ready("SETUP")
            log.warning("Auto")
            self._setup_hall_active = True

        return self._start_hall_job(
            targets=SETUP_DIR_MAP[direction],
            rpm=rpm,
            max_seconds=max_seconds,
            label=f"setup_hall_{direction}",
        )

    def setup_all_run_test(self, rpm: int = 500, seconds: float = 2.0, direction: str = "forward") -> str:
        direction = direction.lower()
        if direction not in {"forward", "reverse"}:
            raise ValueError("invalid direction")
        seconds = max(0.0, float(seconds))
        abs_rpm = max(0, int(rpm))
        motor_dir = "F" if direction == "forward" else "R"

        with self._command_lock:
            with self._lock:
                self._ensure_ready("SETUP")
            if not self._motion_allowed_or_stopped(allow_hall_below=True):
                raise RuntimeError("all-winch ACK test blocked by safety")

            log.info(f"ALL ACK TEST BEGIN dir={direction} rpm={abs_rpm} sec={seconds}")
            try:
                for motor_id in WINCH_IDS:
                    log.info(f"ALL ACK TEST rpm motor={motor_id} rpm={abs_rpm} wait_response=True")
                    rpm_response = self.motor.write_rpm(abs_rpm, motor_id, wait_response=True)
                    if not rpm_response:
                        log.error(f"RPM FAILED: no ACK from motor {motor_id}")

                    log.info(f"ALL ACK TEST start motor={motor_id} dir={motor_dir} wait_response=True")
                    start_response = self.motor.start(motor_dir, motor_id, wait_response=True)
                    if not start_response:
                        log.error(f"START FAILED: no ACK from motor {motor_id}")

                    state = self._motor_state[motor_id]
                    state["running"] = bool(start_response)
                    state["rpm"] = abs_rpm if start_response else 0
                    state["dir"] = motor_dir if start_response else None

                time.sleep(seconds)
            finally:
                self._brake_stop_targets(list(WINCH_IDS), wait_response=False)
                log.info("ALL ACK TEST END")
        return "setup_all_run_test"

    def test_up(self, rpm: int = 350, seconds: float = 10.0) -> str:
        with self._lock:
            self._ensure_ready("TEST")
            if not self.setup_activated:
                raise RuntimeError("setup must be activated before test")
        return self._start_job(
            targets=DIRECTION_MAP["up"],
            rpm=rpm,
            seconds=seconds,
            label="test_up",
            allow_hall_below=True,
        )

    def test_direction(self, name: str, rpm: int = 350, seconds: float = 6.0) -> str:
        name = name.lower()
        if name not in DIRECTION_MAP:
            raise ValueError("invalid direction")
        with self._lock:
            self._ensure_ready("TEST")
            status = self._safety.evaluate(self.last_halls, self.last_update)
            if not status.can_move:
                raise RuntimeError(f"directional tests blocked: {status.reason}")
        return self._start_job(targets=DIRECTION_MAP[name], rpm=rpm, seconds=seconds, label=f"dir_{name}")

    def manual_action(self, action: str, rpm: int = 250, wait_response: bool = True) -> None:
        action = action.lower()
        if action in {"", "none", "stop"}:
            self.stop_all("manual controller stop", wait_response=False)
            return
        if action not in DIRECTION_MAP:
            raise ValueError("invalid manual action")
        with self._lock:
            self._ensure_ready("TEST")
        self._command_motors(DIRECTION_MAP[action], rpm, wait_response=wait_response)

    def manual_winch_action(
        self,
        target: int | str,
        direction: str,
        rpm: int = 250,
        allow_hall_below: bool = False,
        force: bool = False,
        wait_response: bool = True,
    ) -> None:
        direction = direction.lower()
        if direction in {"", "none", "stop"}:
            self.stop_all("manual controller stop", wait_response=False)
            return
        if direction not in {"forward", "reverse"}:
            raise ValueError("invalid winch direction")
        with self._command_lock:
            with self._lock:
                self._ensure_ready("SETUP")
                self._allow_hall_below = bool(allow_hall_below)

            sign = 1 if direction == "forward" else -1
            target_text = str(target).lower()
            if target_text == "all":
                if force:
                    log.info(
                        f"MANUAL WINCH target=all direction={direction} rpm={rpm} "
                        f"force={force} wait_response=False"
                    )
                self._command_all_winch_jog(
                    direction,
                    rpm,
                    allow_hall_below=allow_hall_below,
                    force=force,
                )
                return

            winch_id = int(target)
            if winch_id not in WINCH_IDS:
                raise ValueError("invalid winch id")
            targets = [sign if motor_id == winch_id else 0 for motor_id in WINCH_IDS]
            if force:
                log.info(
                    f"MANUAL WINCH target={target} direction={direction} rpm={rpm} "
                    f"targets={targets} force={force} wait_response={wait_response}"
                )
            self._command_motors(
                targets,
                rpm,
                allow_hall_below=allow_hall_below,
                force=force,
                wait_response=wait_response,
            )

    def selected_winch_stop(
        self,
        target: int | str,
        natural_all_after_brake: bool = False,
        wait_response: bool = True,
        natural_after_delay_s: float = 0.4,
        repeat_brake: bool = False,
        repeat_brake_after_s: float = 0.1,
    ) -> None:
        with self._command_lock:
            target_text = str(target).lower()
            target_all = target_text == "all"
            if target_all:
                brake_targets = list(WINCH_IDS)
                wait_response = False
                repeat_brake = True
                log.info("ALL STOP BEGIN")
            else:
                winch_id = int(target)
                if winch_id not in WINCH_IDS:
                    raise ValueError("invalid winch id")
                brake_targets = [winch_id]

            self._brake_stop_targets(brake_targets, wait_response=wait_response)

            if repeat_brake and target_all:
                delay_s = max(0.08, min(0.12, float(repeat_brake_after_s)))
                time.sleep(delay_s)
                self._brake_stop_targets(brake_targets, wait_response=False)

            if target_all:
                log.info("ALL STOP END")

            if natural_all_after_brake:
                self._natural_stop_all_after_delay(natural_after_delay_s)

    def _brake_stop_targets(self, brake_targets: List[int], wait_response: bool) -> None:
        for motor_id in brake_targets:
            log.info(f"MOTOR cmd brake-stop motor={motor_id} wait_response={wait_response}")
            stop_response = self._stop_motor(motor_id, force=True, wait_response=wait_response, brake=True)
            if wait_response and not stop_response:
                log.error(f"STOP FAILED: no ACK from motor {motor_id}")

    def _natural_stop_all_after_delay(self, delay_s: float) -> None:
        delay_s = max(0.3, min(0.5, float(delay_s)))

        def stop_later():
            time.sleep(delay_s)
            with self._command_lock:
                for motor_id in WINCH_IDS:
                    log.info(f"MOTOR cmd delayed natural-stop motor={motor_id} wait_response=False")
                    self._stop_motor(motor_id, force=True, wait_response=False, brake=False)

        threading.Thread(target=stop_later, daemon=True).start()

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
                    mode = self.mode
                    imu = self.last_imu
                    updated = self.last_update

                if mode != "TEST":
                    time.sleep(0.05)
                    continue

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

                log.debug(
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
        if not getattr(self, "motor_available", True):
            raise RuntimeError(f"motor RS485 unavailable: {getattr(self, 'motor_error', 'unknown')}")
        if self.fault:
            raise RuntimeError(f"FAULT: {self.fault}")
        if self.mode != required_mode:
            raise RuntimeError(f"mode must be {required_mode}")
        if self._job_thread is not None and self._job_thread.is_alive():
            raise RuntimeError("another job running")

    def _start_job(
        self,
        targets: List[int],
        rpm: int,
        seconds: float,
        label: str,
        allow_hall_below: bool = False,
    ) -> str:
        job = threading.Thread(
            target=self._run_job,
            args=(targets, rpm, seconds, label, allow_hall_below),
            daemon=True,
        )
        self._job_thread = job
        log.info(f"Job start: {label} rpm={rpm} sec={seconds}")
        job.start()
        return label

    def _start_hall_job(self, targets: List[int], rpm: int, max_seconds: Optional[float], label: str) -> str:
        job = threading.Thread(target=self._run_hall_job, args=(targets, rpm, max_seconds, label), daemon=True)
        self._job_thread = job
        log.info(f"Hall job start: {label} rpm={rpm} max_sec={max_seconds}")
        job.start()
        return label

    def _run_job(self, targets: List[int], rpm: int, seconds: float, label: str, allow_hall_below: bool):
        try:
            with self._lock:
                self._allow_hall_below = allow_hall_below
            self._command_motors(targets, rpm)
            time.sleep(max(0.0, seconds))
        except Exception as exc:  # safety: any failure → fault
            self.safe_brake_all(f"job error {label}: {exc}")
            return
        finally:
            with self._lock:
                faulted = self.mode == "FAULT" or self.fault is not None
            if not faulted:
                self._stop_motors("job finished")
            with self._lock:
                self._job_thread = None
                self._allow_hall_below = False

    def _run_hall_job(self, targets: List[int], rpm: int, max_seconds: Optional[float], label: str):
        start = time.time()

        log.info(f"Hall job running: {label}")
        try:
            while True:
                wait_for_hall = False
                fault_reason = None
                with self._lock:
                    status = self._safety.evaluate(self.last_halls, self.last_update)
                    if not status.can_move:
                        if status.reason and status.reason.startswith("hall below"):
                            # Arm until hall is above threshold; keep motors stopped.
                            self._stop_motors_locked("hall below threshold")
                            wait_for_hall = True
                        else:
                            fault_reason = status.reason or "safety stop"

                if fault_reason:
                    log.warning(f"Motion blocked: {fault_reason}")
                    self.safe_brake_all(fault_reason)
                    return

                if wait_for_hall:
                    time.sleep(POLL_INTERVAL)
                    continue

                self._command_motors(targets, rpm)
                if max_seconds is not None and (time.time() - start) >= max_seconds:
                    break
                time.sleep(POLL_INTERVAL)
        except Exception as exc:  # safety: any failure → fault
            self.safe_brake_all(f"job error {label}: {exc}")
            return
        finally:
            with self._lock:
                faulted = self.mode == "FAULT" or self.fault is not None
            if not faulted:
                self._stop_motors("hall job finished")
            with self._lock:
                self._job_thread = None
                self._setup_hall_active = False

    def _command_motors(
        self,
        targets: List[int],
        rpm: int,
        allow_hall_below: bool = False,
        force: bool = False,
        wait_response: bool = True,
    ):
        with self._command_lock:
            if not self._motion_allowed_or_stopped(allow_hall_below):
                return
            abs_rpm = max(0, int(rpm))
            for motor_id, direction in zip(WINCH_IDS, targets):
                state = self._motor_state[motor_id]
                if direction == 0:
                    if force and not state["running"]:
                        continue
                    if force:
                        log.info(f"MOTOR cmd stop motor={motor_id} force={force}")
                    self._stop_motor(motor_id, force=force, wait_response=wait_response)
                    continue
                desired_dir = "F" if direction > 0 else "R"
                command_failed = False
                if force or (not state["running"]) or state["rpm"] != abs_rpm:
                    log.info(f"MOTOR cmd rpm motor={motor_id} rpm={abs_rpm} wait_response={wait_response}")
                    rpm_response = self.motor.write_rpm(abs_rpm, motor_id, wait_response=wait_response)
                    if wait_response and not rpm_response:
                        command_failed = True
                        log.warning(f"MOTOR no-ack rpm motor={motor_id} rpm={abs_rpm}")
                        log.error(f"RPM FAILED: no ACK from motor {motor_id}")
                if force or (not state["running"]) or state["dir"] != desired_dir:
                    log.info(f"MOTOR cmd start motor={motor_id} dir={desired_dir} wait_response={wait_response}")
                    start_response = self.motor.start(desired_dir, motor_id, wait_response=wait_response)
                    if wait_response and not start_response:
                        command_failed = True
                        log.warning(f"MOTOR no-ack start motor={motor_id} dir={desired_dir}")
                        log.error(f"START FAILED: no ACK from motor {motor_id}")
                if command_failed:
                    state["running"] = False
                    state["rpm"] = 0
                    state["dir"] = None
                    continue
                state["rpm"] = abs_rpm
                state["dir"] = desired_dir
                state["running"] = True

    def _command_all_winch_jog(
        self,
        direction: str,
        rpm: int,
        allow_hall_below: bool = False,
        force: bool = False,
    ) -> None:
        with self._command_lock:
            if not self._motion_allowed_or_stopped(allow_hall_below):
                return
            abs_rpm = max(0, int(rpm))
            desired_dir = "F" if direction == "forward" else "R"
            rpm_targets = []
            start_targets = []
            for motor_id in WINCH_IDS:
                state = self._motor_state[motor_id]
                if force or (not state["running"]) or state["rpm"] != abs_rpm:
                    rpm_targets.append(motor_id)
                if force or (not state["running"]) or state["dir"] != desired_dir:
                    start_targets.append(motor_id)

            if not rpm_targets and not start_targets:
                return

            log.info(f"ALL MOVE BEGIN target=all dir={direction} rpm={abs_rpm}")
            for motor_id in rpm_targets:
                log.info(f"MOTOR cmd rpm motor={motor_id} rpm={abs_rpm} wait_response=False")
                self.motor.write_rpm(abs_rpm, motor_id, wait_response=False)
            for motor_id in start_targets:
                log.info(f"MOTOR cmd start motor={motor_id} dir={desired_dir} wait_response=False")
                self.motor.start(desired_dir, motor_id, wait_response=False)
            for motor_id in WINCH_IDS:
                state = self._motor_state[motor_id]
                state["running"] = True
                state["rpm"] = abs_rpm
                state["dir"] = desired_dir
            log.info("ALL MOVE END")

    def _motion_allowed_or_stopped(self, allow_hall_below: bool) -> bool:
        fault_reason = None
        with self._lock:
            if self.mode not in {"SETUP", "TEST"}:
                self._stop_motors_locked("mode not armed")
                return False
            status = self._safety.evaluate(self.last_halls, self.last_update)
            if status.can_move:
                return True
            if status.reason and status.reason.startswith("hall below"):
                if self._allow_hall_below or allow_hall_below:
                    return True
                if self._setup_hall_active:
                    log.info(f"Setup hall stop: {status.reason}")
                    self._stop_motors_locked("hall below threshold")
                    return False
                log.warning(f"Motion blocked: {status.reason}")
                self._stop_motors_locked("hall below threshold")
                return False
            fault_reason = status.reason or "safety stop"
        log.warning(f"Motion blocked: {fault_reason}")
        self._safe_brake_all_under_command_lock(fault_reason)
        return False

    def _stop_motor(
        self,
        motor_id: int,
        force: bool = False,
        wait_response: bool = False,
        brake: bool = False,
    ):
        state = self._motor_state[motor_id]
        if not force and not state["running"]:
            return b""
        stop_response = None
        try:
            stop_response = self.motor.stop(motor_id, wait_response=wait_response, brake=brake)
            if wait_response and not stop_response:
                log.warning(f"MOTOR no-ack stop motor={motor_id}")
        except Exception:
            stop_response = None
        state["running"] = False
        state["rpm"] = 0
        state["dir"] = None
        return stop_response

    def _stop_motors(
        self,
        reason: str = "",
        force: bool = False,
        wait_response: bool = False,
        brake: bool = False,
    ):
        with self._command_lock:
            with self._lock:
                self._stop_motors_locked(reason, force=force, wait_response=wait_response, brake=brake)

    def _stop_motors_locked(
        self,
        reason: str = "",
        force: bool = False,
        wait_response: bool = False,
        brake: bool = False,
    ):
        for mid in WINCH_IDS:
            self._stop_motor(mid, force=force, wait_response=wait_response, brake=brake)

    def _can_move_locked(self) -> bool:
        # caller must hold lock
        status = self._safety.evaluate(self.last_halls, self.last_update)
        return status.can_move

    # ------------- polling & safety -------------
    def _poll_loop(self):
        backoff = EVB_BACKOFF_INITIAL
        while not self._stop_event.is_set():
            try:
                with EVBDriver(self.host, self.port, self.timeout) as evb:
                    had_error = False
                    while not self._stop_event.is_set():
                        cycle_start = time.time()
                        halls = {}
                        power = {}
                        bundles = {}
                        try:
                            for w in WINCH_IDS:
                                if USE_BUNDLE:
                                    bundle = evb.bundle(w)
                                    halls[w] = bundle.hall_raw
                                    if USE_POWER:
                                        power[w] = {
                                            "bus_mv": bundle.bus_mv,
                                            "current_ma": bundle.current_ma,
                                            "power_mw": bundle.power_mw,
                                        }
                                    bundles[w] = bundle.__dict__
                                else:
                                    snap = evb.snapshot(w)
                                    halls[w] = snap.hall_raw
                                    bundles[w] = {
                                        "winch": snap.winch,
                                        "total_count": snap.total_count,
                                        "hall_raw": snap.hall_raw,
                                    }
                            try:
                                imu = evb.imu() if USE_IMU else None
                            except Exception:
                                imu = None
                        except Exception as exc:
                            now = time.time()
                            with self._lock:
                                self._evb_error_count += 1
                                self._evb_error_streak += 1
                                self._evb_last_error = str(exc)
                                self._evb_last_error_ts = now
                            log.error(
                                f"EVB read error: {exc}; backoff={backoff:.2f}s "
                                f"(count={self._evb_error_count} streak={self._evb_error_streak})"
                            )
                            self.safe_brake_all(f"EVB error: {exc}")
                            had_error = True
                            break

                        now = time.time()
                        fault_reason = None
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
                                    "cache_age_ms": imu.cache_age_ms,
                                }
                            self.last_update = now
                            status = self._safety.evaluate(self.last_halls, self.last_update)
                            if not status.can_move:
                                if status.reason and status.reason.startswith("hall below"):
                                    if self._allow_hall_below:
                                        pass
                                    elif self._setup_hall_active:
                                        self._stop_motors_locked("hall below threshold")
                                    else:
                                        self._stop_motors_locked("hall below threshold")
                                else:
                                    fault_reason = status.reason or "safety stop"
                            log.debug(
                                f"EVB sample halls={self.last_halls} power={self.last_power} "
                                f"imu={'ok' if self.last_imu else 'none'}"
                            )
                        if fault_reason:
                            self.safe_brake_all(fault_reason)

                        elapsed = time.time() - cycle_start
                        sleep_for = max(0.0, POLL_INTERVAL - elapsed)
                        time.sleep(sleep_for)
                    if had_error:
                        log.warning(f"EVB retrying after error; backoff={backoff:.2f}s")
                        time.sleep(backoff)
                        backoff = min(EVB_BACKOFF_MAX, backoff * EVB_BACKOFF_FACTOR)
                    else:
                        with self._lock:
                            self._evb_error_streak = 0
                        backoff = EVB_BACKOFF_INITIAL
            except Exception as exc:
                now = time.time()
                with self._lock:
                    self._evb_error_count += 1
                    self._evb_error_streak += 1
                    self._evb_last_error = str(exc)
                    self._evb_last_error_ts = now
                log.error(
                    f"EVB connection failure: {exc}; backoff={backoff:.2f}s "
                    f"(count={self._evb_error_count} streak={self._evb_error_streak})"
                )
                self.safe_brake_all(f"EVB connection failure: {exc}")
                time.sleep(backoff)
                backoff = min(EVB_BACKOFF_MAX, backoff * EVB_BACKOFF_FACTOR)

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


def get_controller(**kwargs) -> MotionController:
    global controller
    if controller is None:
        controller = MotionController(**kwargs)
    return controller


if __name__ == "__main__":
    mc = get_controller()
    log.info("MotionController running. Press Ctrl-C to exit.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        mc.shutdown()
