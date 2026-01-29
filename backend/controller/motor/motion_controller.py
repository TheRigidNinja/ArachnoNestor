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
HALL_MAX = CONFIG["motion"].get("hall_max", 2800)
HALL_RPM_MAX = CONFIG["motion"].get("hall_rpm_max", 1500)
HALL_RPM_MIN = CONFIG["motion"].get("hall_rpm_min", 200)
POLL_INTERVAL = CONFIG["motion"]["poll_interval"]
STALE_TIMEOUT = CONFIG["motion"]["stale_timeout"]
EVB_BACKOFF_INITIAL = CONFIG["motion"]["evb_backoff_initial"]
EVB_BACKOFF_MAX = CONFIG["motion"]["evb_backoff_max"]
EVB_BACKOFF_FACTOR = CONFIG["motion"]["evb_backoff_factor"]
USE_BUNDLE = CONFIG["motion"].get("use_bundle", True)
USE_POWER = CONFIG["motion"].get("use_power", True)
USE_IMU = CONFIG["motion"].get("use_imu", True)
DEFAULT_DEVICE_ADDRESS = CONFIG["motion"].get("device_address", 1)
MODBUS_ADDRESSES = CONFIG["motion"].get("modbus_addresses")

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
                 baud_rate: int | None = CONFIG["motion"]["baud_rate"]):
        self.host = host
        self.port = port
        self.timeout = timeout

        # Shared state guarded by lock
        # RLock because some helpers (e.g. record_command) are called from code paths
        # that already hold the controller lock.
        self._lock = threading.RLock()
        self.mode: str = "IDLE"
        self.fault: Optional[str] = None
        self.setup_activated: bool = False
        self.last_halls: Dict[int, int] = {w: 0 for w in WINCH_IDS}
        self.last_power: Dict[int, Dict[str, int]] = {w: {"bus_mv": 0, "current_ma": 0, "power_mw": 0} for w in WINCH_IDS}
        self.last_bundle: Dict[int, Dict[str, int]] = {w: {} for w in WINCH_IDS}
        self.last_imu: Optional[Dict[str, float]] = None
        self.last_update: Optional[float] = None
        self.last_command: Optional[Dict[str, str]] = None
        self.last_hall_cmd: Dict[int, Dict[str, float | int | str]] = {}
        self._poll_seq: int = 0
        self._max_hall_seen: Dict[int, int] = {w: 0 for w in WINCH_IDS}
        self._last_modbus_warn_ts: Dict[tuple[int, str], float] = {}

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
        self._job_label: Optional[str] = None
        # Incremented whenever we start/cancel a job. A job thread exits when its generation is stale.
        self._job_gen: int = 0
        self._setup_hall_active = False
        self._allow_hall_below = False
        self._evb_error_count = 0
        self._evb_error_streak = 0
        self._evb_last_error = None
        self._evb_last_error_ts = None

        # Track last command per motor to avoid spamming Modbus
        self._motor_state = {w: {"running": False, "rpm": 0, "dir": None} for w in WINCH_IDS}
        self._safety = SafetyMonitor(hall_threshold=HALL_THRESHOLD, stale_timeout_s=STALE_TIMEOUT)
        self._motor_addr = self._build_motor_addr()

    def _build_motor_addr(self) -> Dict[int, int]:
        if MODBUS_ADDRESSES is None:
            return {w: DEFAULT_DEVICE_ADDRESS for w in WINCH_IDS}
        if isinstance(MODBUS_ADDRESSES, list):
            if len(MODBUS_ADDRESSES) != len(WINCH_IDS):
                log.warning("modbus_addresses length mismatch; defaulting to device_address")
                return {w: DEFAULT_DEVICE_ADDRESS for w in WINCH_IDS}
            return {w: int(addr) for w, addr in zip(WINCH_IDS, MODBUS_ADDRESSES)}
        if isinstance(MODBUS_ADDRESSES, dict):
            return {int(k): int(v) for k, v in MODBUS_ADDRESSES.items()}
        log.warning("modbus_addresses invalid; defaulting to device_address")
        return {w: DEFAULT_DEVICE_ADDRESS for w in WINCH_IDS}

    def _apply_motor_ops(self, ops: list[tuple[str, int, object]]):
        """
        Apply motor hardware operations *without* holding self._lock.
        ops items: (op, slave, arg) where op in {"stop","rpm","start"}.
        """
        for op, slave, arg in ops:
            try:
                resp = None
                if op == "stop":
                    resp = self.motor.stop(slave=slave)
                elif op == "rpm":
                    resp = self.motor.write_rpm(int(arg), slave=slave)
                elif op == "start":
                    resp = self.motor.start(str(arg), slave=slave)
                if resp is None:
                    # Rate-limit warnings to avoid spamming the console during control loops.
                    key = (int(slave), op)
                    now = time.time()
                    last = self._last_modbus_warn_ts.get(key, 0.0)
                    if now - last > 1.0:
                        self._last_modbus_warn_ts[key] = now
                        log.warning(f"MODBUS: no response slave={slave} op={op}")
            except Exception:
                # Safety: ignore individual bus errors here; poll loop will fault on EVB comms separately.
                pass

    def _stop_motors_hw_all(self) -> None:
        # Hardware IO (serial) can block; don't hold self._lock while calling this.
        for motor_id in WINCH_IDS:
            slave = self._motor_addr.get(motor_id, DEFAULT_DEVICE_ADDRESS)
            try:
                self.motor.stop(slave=slave)
            except Exception:
                pass

    # ------------- public API (used by web server or main) -------------
    def get_status(self) -> dict:
        with self._lock:
            halls = {str(k): v for k, v in self.last_halls.items()}
            power = {str(k): v.copy() for k, v in self.last_power.items()}
            bundles = {str(k): v.copy() for k, v in self.last_bundle.items()}
            imu = self.last_imu.copy() if self.last_imu else None
            last_cmd = self.last_command.copy() if self.last_command else None
            job_running = self._job_thread.is_alive() if self._job_thread else False
            last_hall_cmd = {str(k): v.copy() for k, v in self.last_hall_cmd.items()}
            max_hall_seen = {str(k): v for k, v in self._max_hall_seen.items()}
            return {
                "mode": self.mode,
                "fault": self.fault,
                "setup_activated": self.setup_activated,
                "evb_error_count": self._evb_error_count,
                "evb_error_streak": self._evb_error_streak,
                "evb_last_error": self._evb_last_error,
                "evb_last_error_ts": self._evb_last_error_ts,
                "halls": halls,
                "power": power,
                "bundles": bundles,
                "imu": imu,
                "threshold": HALL_THRESHOLD,
                "last_update": self.last_update,
                "job_active": self._job_thread is not None and self._job_thread.is_alive(),
                "job_label": self._job_label,
                "job_gen": self._job_gen,
                "job_running": job_running,
                "last_command": last_cmd,
                "last_hall_cmd": last_hall_cmd,
                "poll_seq": self._poll_seq,
                "max_hall_seen": max_hall_seen,
            }

    def record_command(self, action: str, detail: str = "") -> None:
        with self._lock:
            self.last_command = {
                "ts": str(time.time()),
                "action": str(action),
                "detail": str(detail),
            }

    def set_mode(self, mode: str) -> None:
        mode = mode.upper()
        if mode not in {"IDLE", "SETUP", "TEST", "FAULT"}:
            raise ValueError("invalid mode")
        with self._lock:
            # If already faulted, only allow clear_fault to exit
            if self.fault and mode != "FAULT":
                raise RuntimeError("in FAULT; clear_fault first")
            if mode == "IDLE":
                self.setup_activated = False
            # Request stop before switching (HW stop happens outside the lock).
            for mid in WINCH_IDS:
                st = self._motor_state.get(mid)
                if st is not None:
                    st["running"] = False
            self.mode = mode
            if mode == "SETUP":
                self.setup_activated = True
                # Setup mode no longer auto-starts a hall job; UI must explicitly press Run Hall.
                pass
        self._stop_motors_hw_all()

    def clear_fault(self) -> None:
        with self._lock:
            self.fault = None
            self.mode = "IDLE"
            self.setup_activated = False
            for mid in WINCH_IDS:
                st = self._motor_state.get(mid)
                if st is not None:
                    st["running"] = False
        self._stop_motors_hw_all()

    def stop_all(self, reason: str = "user stop", as_fault: bool = False) -> None:
        """Stop motors. If as_fault=True, enter FAULT mode and record reason."""
        with self._lock:
            self._job_gen += 1
            for mid in WINCH_IDS:
                st = self._motor_state.get(mid)
                if st is not None:
                    st["running"] = False
            if as_fault:
                self.fault = reason if self.fault is None else self.fault
                self.mode = "FAULT"
        self._stop_motors_hw_all()

    def emergency_stop(self, reason: str = "emergency stop") -> None:
        """Force-stop and enter FAULT regardless of current state."""
        self.stop_all(reason=reason, as_fault=True)

    def cancel_job(self, reason: str = "user cancel") -> None:
        """Force-cancel the active job, clear flags, and stop motors."""

        log.info(f"Cancel job: {reason}")
        with self._lock:
            self._job_gen += 1
            for mid in WINCH_IDS:
                st = self._motor_state.get(mid)
                if st is not None:
                    st["running"] = False
            self._job_thread = None
            self._setup_hall_active = False
            self._allow_hall_below = False
        # Avoid blocking the web/SSE threads on serial I/O; stop motors asynchronously.
        threading.Thread(target=self._stop_motors_hw_all, daemon=True).start()

    def setup_jog(self, rpm: int = 200, seconds: float = 1.0) -> str:
        with self._lock:
            self._ensure_ready("SETUP")
        return self._start_job(
            targets=DIRECTION_MAP["up"],
            rpm=rpm,
            seconds=seconds,
            label="setup_jog",
            allow_hall_below=True,
        )

    def setup_hall_run(self, rpm: int = 200, seconds: float = 0.0, direction: str = "forward") -> str:
        direction = direction.lower()
        if direction not in SETUP_DIR_MAP:
            raise ValueError("invalid direction")
        log.debug("setup_hall_run: entering")
        # Always run until user cancels/stops; ignore any UI-provided seconds.
        max_seconds = None

        # Stop any prior job/motion without holding the controller lock during serial I/O,
        # otherwise the /events stream can stall while waiting for self._lock.
        self.cancel_job("pre hall start")

        log.debug("setup_hall_run: acquired cancel lock")
        with self._lock:
            self._ensure_ready("SETUP")
            self._setup_hall_active = True
            self._allow_hall_below = True
            self.record_command("setup_hall_arm", f"dir={direction} max_sec={max_seconds}")

        # Keep this line visible even when log.mode=ui_only (allow_prefixes includes "setup_hall_run:")
        log.debug("setup_hall_run: starting hall job thread")
        return self._start_hall_job(
            targets=SETUP_DIR_MAP[direction],
            rpm=HALL_RPM_MAX,
            max_seconds=max_seconds,
            label=f"setup_hall_{direction}",
        )

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
        if self._job_thread is not None and not self._job_thread.is_alive():
            self._job_thread = None
            self._job_label = None
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
        with self._lock:
            self._job_gen += 1
            job_gen = self._job_gen
            job = threading.Thread(
                target=self._run_job,
                args=(job_gen, targets, rpm, seconds, label, allow_hall_below),
                daemon=True,
            )
            self._job_thread = job
            self._job_label = label
        log.info(f"Job start: {label} rpm={rpm} sec={seconds}")
        job.start()
        return label

    def _start_hall_job(self, targets: List[int], rpm: int, max_seconds: Optional[float], label: str) -> str:
        with self._lock:
            self._job_gen += 1
            job_gen = self._job_gen
            job = threading.Thread(
                target=self._run_hall_job,
                args=(job_gen, targets, rpm, max_seconds, label),
                daemon=True,
            )
            self._job_thread = job
            self._job_label = label
        log.info(f"Hall job start: {label} rpm={rpm} max_sec={max_seconds}")
        job.start()
        return label

    def _run_job(self, job_gen: int, targets: List[int], rpm: int, seconds: float, label: str, allow_hall_below: bool):
        try:
            with self._lock:
                self._allow_hall_below = allow_hall_below
            self._command_motors(targets, rpm)
            end_time = time.time() + max(0.0, seconds)
            while time.time() < end_time:
                if job_gen != self._job_gen:
                    break
                time.sleep(min(POLL_INTERVAL, max(0.0, end_time - time.time())))
        except Exception as exc:  # safety: any failure → fault
            self.stop_all(f"job error {label}: {exc}", as_fault=True)
            return
        finally:
            self._stop_motors("job finished")
            with self._lock:
                self._job_thread = None
                self._job_label = None
                self._allow_hall_below = False

    def _run_hall_job(self, job_gen: int, targets: List[int], rpm: int, max_seconds: Optional[float], label: str):
        start = time.time()

        log.info(f"Hall job running: {label}")
        try:
            while True:
                if job_gen != self._job_gen:
                    break
                should_hw_stop = False
                with self._lock:
                    status = self._safety.evaluate(self.last_halls, self.last_update)
                    halls = self.last_halls.copy()
                    if not status.can_move and not (status.reason and status.reason.startswith("hall below")):
                        self.fault = status.reason
                        self.mode = "FAULT"
                        log.warning(f"Motion blocked: {status.reason}")
                        self._stop_motors_locked("safety stop")
                        should_hw_stop = True

                if should_hw_stop:
                    self._stop_motors_hw_all()
                    return

                self._command_hall_motors(targets, halls)
                if max_seconds is not None and (time.time() - start) >= max_seconds:
                    break
                time.sleep(POLL_INTERVAL)
        except Exception as exc:  # safety: any failure → fault
            self.stop_all(f"job error {label}: {exc}", as_fault=True)
            return
        finally:
            self._stop_motors("hall job finished")
            with self._lock:
                self._job_thread = None
                self._job_label = None
                self._setup_hall_active = False

    def _command_motors(self, targets: List[int], rpm: int):
        ops: list[tuple[str, int, object]] = []
        abs_rpm = max(0, int(rpm))
        should_return = False
        with self._lock:
            status = self._safety.evaluate(self.last_halls, self.last_update)
            if not status.can_move:
                if status.reason and status.reason.startswith("hall below"):
                    if self._allow_hall_below or self._setup_hall_active:
                        pass
                    else:
                        log.warning(f"Motion blocked: {status.reason}")
                        for mid in WINCH_IDS:
                            st = self._motor_state[mid]
                            if st["running"]:
                                ops.append(("stop", self._motor_addr.get(mid, DEFAULT_DEVICE_ADDRESS), None))
                                st["running"] = False
                        should_return = True
                else:
                    self.fault = status.reason
                    self.mode = "FAULT"
                    log.warning(f"Motion blocked: {status.reason}")
                    for mid in WINCH_IDS:
                        st = self._motor_state[mid]
                        if st["running"]:
                            ops.append(("stop", self._motor_addr.get(mid, DEFAULT_DEVICE_ADDRESS), None))
                            st["running"] = False
                    should_return = True
            if should_return:
                # apply after releasing lock
                pass
            else:
                for motor_id, direction in zip(WINCH_IDS, targets):
                    st = self._motor_state[motor_id]
                    slave = self._motor_addr.get(motor_id, DEFAULT_DEVICE_ADDRESS)
                    if direction == 0:
                        if st["running"]:
                            ops.append(("stop", slave, None))
                            st["running"] = False
                        continue
                    desired_dir = "F" if direction > 0 else "R"
                    if (not st["running"]) or st["rpm"] != abs_rpm:
                        ops.append(("rpm", slave, abs_rpm))
                        st["rpm"] = abs_rpm
                    if (not st["running"]) or st["dir"] != desired_dir:
                        ops.append(("start", slave, desired_dir))
                        st["dir"] = desired_dir
                    st["running"] = True
        self._apply_motor_ops(ops)

    def _hall_to_rpm(self, hall_val: int) -> int:
        if HALL_MAX <= HALL_THRESHOLD:
            return HALL_RPM_MIN
        if hall_val < HALL_THRESHOLD:
            return 0
        span = HALL_MAX - HALL_THRESHOLD
        ratio = (hall_val - HALL_THRESHOLD) / span
        rpm = ratio * HALL_RPM_MAX
        rpm = max(0.0, min(float(HALL_RPM_MAX), rpm))
        rpm = max(float(HALL_RPM_MIN), rpm)
        return int(rpm)

    def _command_hall_motors(self, targets: List[int], halls: Dict[int, int]):
        ops: list[tuple[str, int, object]] = []
        log_lines: list[str] = []
        with self._lock:
            status = self._safety.evaluate(self.last_halls, self.last_update)
            if not status.can_move and not (status.reason and status.reason.startswith("hall below")):
                if status.reason:
                    self.fault = status.reason
                self.mode = "FAULT"
                log.warning(f"Motion blocked: {status.reason}")
                for mid in WINCH_IDS:
                    st = self._motor_state[mid]
                    if st["running"]:
                        ops.append(("stop", self._motor_addr.get(mid, DEFAULT_DEVICE_ADDRESS), None))
                        st["running"] = False
                # apply after releasing lock

            for motor_id, direction in zip(WINCH_IDS, targets):
                st = self._motor_state[motor_id]
                slave = self._motor_addr.get(motor_id, DEFAULT_DEVICE_ADDRESS)
                if direction == 0:
                    if st["running"]:
                        ops.append(("stop", slave, None))
                        st["running"] = False
                    continue

                hall_val = halls.get(motor_id)
                if hall_val is None or hall_val < HALL_THRESHOLD:
                    if st["running"]:
                        ops.append(("stop", slave, None))
                        st["running"] = False
                    continue

                desired_dir = "F" if direction > 0 else "R"
                target_rpm = self._hall_to_rpm(hall_val)
                if (not st["running"]) or st["rpm"] != target_rpm:
                    ops.append(("rpm", slave, target_rpm))
                    st["rpm"] = target_rpm
                if (not st["running"]) or st["dir"] != desired_dir:
                    ops.append(("start", slave, desired_dir))
                    st["dir"] = desired_dir
                st["running"] = True

                self.last_hall_cmd[motor_id] = {
                    "hall": hall_val,
                    "rpm": target_rpm,
                    "dir": desired_dir,
                    "slave": slave,
                    "ts": time.time(),
                }
                log_lines.append(
                    f"Setup hall cmd: winch={motor_id} hall={hall_val} rpm={target_rpm} dir={desired_dir} slave={slave}"
                )

        self._apply_motor_ops(ops)
        for line in log_lines:
            log.info(line)

    def _stop_motors(self, reason: str = ""):
        with self._lock:
            self._stop_motors_locked(reason)
        self._stop_motors_hw_all()

    def _stop_motors_locked(self, reason: str = ""):
        for mid in WINCH_IDS:
            st = self._motor_state.get(mid)
            if st is not None:
                st["running"] = False

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
                            self.stop_all(f"EVB error: {exc}", as_fault=True)
                            had_error = True
                            break

                        now = time.time()
                        should_hw_stop = False
                        with self._lock:
                            self._poll_seq += 1
                            self.last_halls.update(halls)
                            for w, hv in halls.items():
                                if hv > self._max_hall_seen.get(w, 0):
                                    self._max_hall_seen[w] = hv
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
                                if status.reason and status.reason.startswith("hall below"):
                                    if self._allow_hall_below:
                                        pass
                                    elif self._setup_hall_active:
                                        # allow per-motor hall gating during setup hall
                                        pass
                                    else:
                                        self._stop_motors_locked("hall below threshold")
                                        should_hw_stop = True
                                else:
                                    self.fault = status.reason
                                    self.mode = "FAULT"
                                    self._stop_motors_locked("safety stop")
                                    should_hw_stop = True
                            log.info(
                                f"EVB sample halls={self.last_halls} power={self.last_power} "
                                f"imu={'ok' if self.last_imu else 'none'}"
                            )
                        if should_hw_stop:
                            self._stop_motors_hw_all()

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
                self.stop_all(f"EVB connection failure: {exc}", as_fault=True)
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
