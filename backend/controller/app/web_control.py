#!/usr/bin/env python3
"""Minimal Flask web UI for supervisor control."""

import json
import logging
import threading
import time
from pathlib import Path

from flask import Flask, Response, jsonify, request

from config.settings import load_config
from drivers.evb_driver import EVBDriver
from drivers.gamepad import GamepadReader
from motor.motion_controller import get_controller, DIRECTION_MAP, WINCH_IDS
from logutil.logger import get_logger

CONFIG = load_config()

app = Flask(__name__)
logging.getLogger("werkzeug").setLevel(logging.WARNING)
mc = get_controller()
log = get_logger("app.web_control")
GAMEPAD_MAPPING_PATH = Path(__file__).resolve().parents[1] / "config" / "gamepad_mapping.json"
GAMEPAD_COMMAND_REFRESH_SEC = 0.35
GAMEPAD_STOP_REASSERT_SEC = 0.20
GAMEPAD_WATCHDOG_SEC = 2.0
MANUAL_FIRST_COMMAND_WAIT_RESPONSE = bool(CONFIG["motion"].get("manual_first_command_wait_response", False))
GAMEPAD_INPUTS = [
    "axis_0_neg",
    "axis_0_pos",
    "axis_1_neg",
    "axis_1_pos",
    "axis_2_neg",
    "axis_2_pos",
    "axis_3_neg",
    "axis_3_pos",
    "axis_4_neg",
    "axis_4_pos",
    "axis_5_neg",
    "axis_5_pos",
    "dpad_up",
    "dpad_down",
    "dpad_left",
    "dpad_right",
    "button_0",
    "button_1",
    "button_2",
    "button_3",
    "button_4",
    "button_5",
    "button_6",
    "button_7",
    "button_8",
    "button_9",
]
GAMEPAD_ACTIONS = [
    "none",
    "selected_up",
    "selected_down",
    "selected_forward",
    "selected_reverse",
    "previous_winch",
    "next_winch",
    "up",
    "down",
    "forward",
    "back",
    "left",
    "right",
    "stop",
]
DEFAULT_GAMEPAD_MAPPING = {
    "enabled": True,
    "rpm": 250,
    "inputs": {
        "axis_2_pos": "none",
        "axis_5_pos": "none",
        "button_0": "selected_down",
        "button_1": "none",
        "button_2": "none",
        "button_3": "selected_up",
        "button_6": "previous_winch",
        "button_7": "next_winch",
        "dpad_up": "none",
        "dpad_down": "none",
        "dpad_left": "none",
        "dpad_right": "none",
    },
}


# ---------- helpers ----------
def ok(data=None):
    return jsonify({"ok": True, **(data or {})})


def err(msg):
    return jsonify({"ok": False, "error": msg}), 400


def load_gamepad_mapping():
    try:
        with GAMEPAD_MAPPING_PATH.open("r", encoding="utf-8") as f:
            loaded = json.load(f)
    except Exception:
        loaded = {}
    mapping = {
        "enabled": bool(loaded.get("enabled", DEFAULT_GAMEPAD_MAPPING["enabled"])),
        "rpm": int(loaded.get("rpm", DEFAULT_GAMEPAD_MAPPING["rpm"])),
        "inputs": DEFAULT_GAMEPAD_MAPPING["inputs"].copy(),
    }
    if isinstance(loaded.get("inputs"), dict):
        for input_name, action in loaded["inputs"].items():
            if input_name in GAMEPAD_INPUTS and action in GAMEPAD_ACTIONS:
                mapping["inputs"][input_name] = action
    return mapping


def save_gamepad_mapping(mapping):
    GAMEPAD_MAPPING_PATH.parent.mkdir(parents=True, exist_ok=True)
    with GAMEPAD_MAPPING_PATH.open("w", encoding="utf-8") as f:
        json.dump(mapping, f, indent=2, sort_keys=True)
        f.write("\n")


class GamepadControl:
    def __init__(self, motion_controller):
        self.mc = motion_controller
        self._lock = threading.Lock()
        self._thread = None
        self._watchdog_thread = None
        self._stop = threading.Event()
        self.active = False
        self.controller_name = None
        self.last_inputs = []
        self.last_action = "stop"
        self.last_error = None
        self.last_update = None
        self.axes = []
        self.buttons = []
        self.hats = []
        self.input_values = {input_name: 0 for input_name in GAMEPAD_INPUTS}
        self._last_start_attempt = 0.0
        self._monitor_disabled = False
        self.selected_target = "1"
        self._previous_inputs = set()
        self.last_probe = []
        self._last_command_key = None
        self._last_command_ts = 0.0
        self._last_stop_ts = 0.0
        self._last_block_reason = None
        self._command_in_progress = False

    def status(self):
        with self._lock:
            return {
                "active": self.active,
                "controller_name": self.controller_name,
                "last_inputs": list(self.last_inputs),
                "last_action": self.last_action,
                "last_error": self.last_error,
                "last_update": self.last_update,
                "axes": list(self.axes),
                "buttons": list(self.buttons),
                "hats": [list(hat) for hat in self.hats],
                "input_values": dict(self.input_values),
                "selected_target": self.selected_target,
                "last_probe": list(self.last_probe),
                "command_in_progress": self._command_in_progress,
            }

    def start(self):
        with self._lock:
            self._monitor_disabled = False
            self._last_start_attempt = time.time()
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self.last_error = None
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
            if self._watchdog_thread is None or not self._watchdog_thread.is_alive():
                self._watchdog_thread = threading.Thread(target=self._watchdog, daemon=True)
                self._watchdog_thread.start()

    def set_target(self, target: int | str):
        target_text = str(target).lower()
        valid_targets = [str(winch_id) for winch_id in WINCH_IDS] + ["all"]
        if target_text not in valid_targets:
            raise ValueError("invalid controller target")
        with self._lock:
            self.selected_target = target_text

    def _cycle_target(self, step: int):
        valid_targets = [str(winch_id) for winch_id in WINCH_IDS] + ["all"]
        with self._lock:
            current = self.selected_target
            if current not in valid_targets:
                self.selected_target = valid_targets[0 if step > 0 else -1]
                return
            index = valid_targets.index(current)
            self.selected_target = valid_targets[(index + step) % len(valid_targets)]

    def ensure_started(self, retry_seconds: float = 2.0):
        with self._lock:
            if self._monitor_disabled:
                return
            thread_alive = self._thread is not None and self._thread.is_alive()
            recently_tried = (time.time() - self._last_start_attempt) < retry_seconds
        if not thread_alive and not recently_tried:
            self.start()

    def stop(self, disable_monitor: bool = False):
        if disable_monitor:
            with self._lock:
                self._monitor_disabled = True
            self._stop.set()
        try:
            self.mc.manual_action("stop")
        except Exception:
            pass
        if disable_monitor:
            with self._lock:
                self.active = False
                self.last_action = "stop"
        self._last_command_key = None
        self._last_command_ts = 0.0
        self._last_stop_ts = 0.0

    def disable_without_motor_command(self):
        with self._lock:
            self._monitor_disabled = True
            self.active = False
            self.last_action = "stop"
            self._last_command_key = None
            self._last_command_ts = 0.0
            self._last_stop_ts = 0.0
            self._command_in_progress = False
        self._stop.set()

    def _set_command_in_progress(self, value: bool):
        with self._lock:
            self._command_in_progress = value
            self.last_update = time.time()

    def _stop_motors_now(self):
        try:
            mode = self.mc.get_status().get("mode")
            if mode == "SETUP":
                all_stop = str(self.selected_target).lower() == "all"
                self._set_command_in_progress(all_stop)
                try:
                    self.mc.selected_winch_stop(
                        self.selected_target,
                        wait_response=not all_stop,
                        repeat_brake=all_stop,
                    )
                finally:
                    self._set_command_in_progress(False)
            else:
                self.mc.manual_action("stop")
        except Exception:
            pass
        now = time.monotonic()
        with self._lock:
            self.last_action = "stop"
            self._last_command_key = ("watchdog", "stop")
            self._last_command_ts = now
            self._last_stop_ts = now

    def _watchdog(self):
        while not self._stop.is_set():
            with self._lock:
                active = self.active
                last_update = self.last_update
                last_action = self.last_action
                command_in_progress = self._command_in_progress
            stale = last_update is not None and (time.time() - last_update) > GAMEPAD_WATCHDOG_SEC
            if active and stale and last_action != "stop" and not command_in_progress:
                log.warning("GAMEPAD watchdog stop: controller input stale")
                self._stop_motors_now()
            time.sleep(0.05)

    def _input_values(self, snapshot):
        values = {input_name: 0 for input_name in GAMEPAD_INPUTS}
        for index, value in enumerate(snapshot.axes):
            neg_name = f"axis_{index}_neg"
            pos_name = f"axis_{index}_pos"
            if neg_name in values:
                values[neg_name] = value if value < -0.5 else 0
            if pos_name in values:
                values[pos_name] = value if value > 0.5 else 0
        for index, value in enumerate(snapshot.buttons):
            input_name = f"button_{index}"
            if input_name in values:
                values[input_name] = int(value)
        for index, (x_value, y_value) in enumerate(snapshot.hats):
            prefix = "dpad" if index == 0 else f"dpad{index}"
            directions = {
                f"{prefix}_up": 1 if y_value > 0 else 0,
                f"{prefix}_down": 1 if y_value < 0 else 0,
                f"{prefix}_right": 1 if x_value > 0 else 0,
                f"{prefix}_left": 1 if x_value < 0 else 0,
            }
            for input_name, value in directions.items():
                if input_name in values:
                    values[input_name] = value
        return values

    def _mapped_action(self, mapping, input_name):
        return mapping.get("inputs", {}).get(input_name, "none")

    def _motion_action(self, mapping, active_inputs):
        movement_actions = {
            "selected_forward",
            "selected_reverse",
            "selected_up",
            "selected_down",
            "up",
            "down",
            "forward",
            "back",
            "left",
            "right",
            "stop",
        }
        for input_name in GAMEPAD_INPUTS:
            if input_name in active_inputs:
                action = self._mapped_action(mapping, input_name)
                if action in movement_actions:
                    return action
        return "stop"

    def _handle_selection_edges(self, mapping, active_inputs):
        active = set(active_inputs)
        new_inputs = active - self._previous_inputs
        self._previous_inputs = active
        for input_name in GAMEPAD_INPUTS:
            if input_name not in new_inputs:
                continue
            action = self._mapped_action(mapping, input_name)
            if action == "previous_winch":
                self._cycle_target(-1)
            elif action == "next_winch":
                self._cycle_target(1)

    def _run(self):
        reader = None
        controller_was_connected = False
        try:
            reader = GamepadReader()
            controller_was_connected = True
            with self._lock:
                self.active = True
                self.controller_name = reader.name
                self.last_probe = [{
                    "index": 0,
                    "name": reader.name,
                    "axes": len(reader.snapshot().axes),
                    "buttons": len(reader.snapshot().buttons),
                    "hats": len(reader.snapshot().hats),
                }]
            while not self._stop.is_set():
                mapping = load_gamepad_mapping()
                snapshot = reader.snapshot()
                status = self.mc.get_status()
                mode = status.get("mode")
                if mode == "SETUP" and mapping.get("enabled", True):
                    self._handle_selection_edges(mapping, snapshot.active_inputs)
                else:
                    self._previous_inputs = set(snapshot.active_inputs)
                action = self._motion_action(mapping, snapshot.active_inputs)
                input_values = self._input_values(snapshot)
                with self._lock:
                    self.last_inputs = snapshot.active_inputs
                    self.last_action = action
                    self.last_update = time.time()
                    self.axes = snapshot.axes
                    self.buttons = snapshot.buttons
                    self.hats = snapshot.hats
                    self.input_values = input_values

                if not mapping.get("enabled", True):
                    block_reason = "disabled"
                    if block_reason != self._last_block_reason:
                        log.info(f"GAMEPAD blocked reason={block_reason}")
                        self._last_block_reason = block_reason
                    if self._last_command_key != ("disabled", "stop"):
                        try:
                            self.mc.manual_action("stop")
                        except Exception:
                            pass
                        self._last_command_key = ("disabled", "stop")
                    time.sleep(0.02)
                    continue

                if mode not in {"SETUP", "TEST"}:
                    block_reason = f"mode:{mode}"
                    if block_reason != self._last_block_reason:
                        log.info(f"GAMEPAD blocked reason={block_reason}")
                        self._last_block_reason = block_reason
                    self._last_command_key = None
                    time.sleep(0.02)
                    continue

                try:
                    rpm = int(mapping.get("rpm", 250))
                    command_key = (mode, self.selected_target, action, rpm)
                    now = time.monotonic()
                    movement_action = action in {"selected_forward", "selected_up", "selected_reverse", "selected_down"}
                    is_new_command = command_key != self._last_command_key
                    refresh_command = movement_action and (now - self._last_command_ts) >= GAMEPAD_COMMAND_REFRESH_SEC
                    force_command = is_new_command or refresh_command
                    all_target = str(self.selected_target).lower() == "all"
                    if mode == "SETUP" and action in {"selected_forward", "selected_up"}:
                        if force_command:
                            wait_response = False if all_target else MANUAL_FIRST_COMMAND_WAIT_RESPONSE and is_new_command
                            force_write = is_new_command
                            log.info(
                                f"GAMEPAD command mode={mode} target={self.selected_target} "
                                f"input={snapshot.active_inputs} action={action} dir=forward rpm={rpm} "
                                f"force={force_write} wait_response={wait_response}"
                            )
                            self._set_command_in_progress(all_target)
                            try:
                                self.mc.manual_winch_action(
                                    self.selected_target,
                                    "forward",
                                    rpm=rpm,
                                    allow_hall_below=True,
                                    force=force_write,
                                    wait_response=wait_response,
                                )
                            finally:
                                self._set_command_in_progress(False)
                            self._last_command_key = command_key
                            self._last_command_ts = time.monotonic()
                    elif mode == "SETUP" and action in {"selected_reverse", "selected_down"}:
                        if force_command:
                            wait_response = False if all_target else MANUAL_FIRST_COMMAND_WAIT_RESPONSE and is_new_command
                            force_write = is_new_command
                            log.info(
                                f"GAMEPAD command mode={mode} target={self.selected_target} "
                                f"input={snapshot.active_inputs} action={action} dir=reverse rpm={rpm} "
                                f"force={force_write} wait_response={wait_response}"
                            )
                            self._set_command_in_progress(all_target)
                            try:
                                self.mc.manual_winch_action(
                                    self.selected_target,
                                    "reverse",
                                    rpm=rpm,
                                    allow_hall_below=True,
                                    force=force_write,
                                    wait_response=wait_response,
                                )
                            finally:
                                self._set_command_in_progress(False)
                            self._last_command_key = command_key
                            self._last_command_ts = time.monotonic()
                    elif mode == "TEST" and action not in {"selected_forward", "selected_reverse", "selected_up", "selected_down"}:
                        if force_command:
                            log.info(
                                f"GAMEPAD command mode={mode} target={self.selected_target} "
                                f"input={snapshot.active_inputs} action={action} rpm={rpm}"
                            )
                            self.mc.manual_action(action, rpm=rpm, wait_response=False)
                            self._last_command_key = command_key
                            self._last_command_ts = now
                    else:
                        stop_key = (mode, "stop")
                        previous_command_key = self._last_command_key
                        previous_was_setup_move = (
                            isinstance(previous_command_key, tuple)
                            and len(previous_command_key) == 4
                            and previous_command_key[0] == "SETUP"
                            and previous_command_key[2] in {
                                "selected_forward",
                                "selected_up",
                                "selected_reverse",
                                "selected_down",
                            }
                        )
                        if mode == "SETUP":
                            should_stop = previous_was_setup_move
                        else:
                            should_stop = (
                                self._last_command_key != stop_key
                                or (now - self._last_stop_ts) >= GAMEPAD_STOP_REASSERT_SEC
                            )
                        if should_stop:
                            stop_target = (
                                previous_command_key[1]
                                if mode == "SETUP" and previous_was_setup_move
                                else self.selected_target
                            )
                            if self._last_command_key != stop_key:
                                log.info(
                                    f"GAMEPAD stop mode={mode} target={stop_target} "
                                    f"input={snapshot.active_inputs} action={action}"
                                )
                            if mode == "SETUP":
                                all_stop = str(stop_target).lower() == "all"
                                self._set_command_in_progress(all_stop)
                                try:
                                    self.mc.selected_winch_stop(
                                        stop_target,
                                        natural_all_after_brake=False,
                                        wait_response=not all_stop,
                                        repeat_brake=all_stop,
                                    )
                                finally:
                                    self._set_command_in_progress(False)
                            else:
                                self.mc.manual_action("stop")
                            self._last_command_key = stop_key
                            self._last_command_ts = time.monotonic()
                            self._last_stop_ts = self._last_command_ts
                    with self._lock:
                        self.last_error = None
                    self._last_block_reason = None
                except Exception as exc:
                    log.error(
                        f"GAMEPAD dispatch error mode={mode} target={self.selected_target} "
                        f"input={snapshot.active_inputs} action={action} error={exc}"
                    )
                    try:
                        self.mc.manual_action("stop")
                    except Exception:
                        pass
                    self._last_command_key = None
                    self._last_command_ts = 0.0
                    with self._lock:
                        self.last_error = str(exc)
                time.sleep(0.02)
        except Exception as exc:
            try:
                probe = GamepadReader.devices()
            except Exception:
                probe = []
            if controller_was_connected and not self._stop.is_set():
                reason = f"controller connection lost: {exc}"
                log.error(reason)
                try:
                    self.mc.emergency_stop(reason)
                except Exception:
                    try:
                        self.mc.stop_all(reason, as_fault=True)
                    except Exception:
                        pass
            with self._lock:
                self.last_error = str(exc)
                self.last_probe = probe
        finally:
            if reader is not None:
                reader.close()
            with self._lock:
                send_final_stop = not self._monitor_disabled
            if send_final_stop:
                try:
                    self.mc.manual_action("stop")
                except Exception:
                    pass
            with self._lock:
                self.active = False


gamepad_control = GamepadControl(mc)


# ---------- routes ----------
@app.get("/status")
def status():
    try:
        return jsonify(mc.get_status())
    except Exception as exc:
        log.error(f"/status error: {exc}")
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.get("/events")
def events():
    def stream():
        last_sent = 0.0
        last_payload = None
        min_interval = 0.1
        heartbeat = 1.0
        while True:
            try:
                data = mc.get_status()
                payload = json.dumps(data, sort_keys=True)
                now = time.time()
                changed = payload != last_payload
                if changed and (now - last_sent) >= min_interval:
                    yield f"data: {payload}\n\n"
                    last_payload = payload
                    last_sent = now
                elif (now - last_sent) >= heartbeat:
                    yield f"data: {payload}\n\n"
                    last_sent = now
            except Exception as exc:
                log.error(f"/events error: {exc}")
                yield f"data: {json.dumps({'ok': False, 'error': str(exc)})}\n\n"
            time.sleep(0.02)
    return Response(stream(), mimetype="text/event-stream")


def _dataclass_dict(obj):
    return obj.__dict__.copy()


def _capture_read(errors, label, func):
    try:
        return func()
    except Exception as exc:
        errors.append({"source": label, "error": str(exc)})
        return None


@app.get("/evb/live")
def evb_live():
    host = CONFIG["evb"]["host"]
    port = CONFIG["evb"]["port"]
    timeout = CONFIG["evb"]["timeout"]
    winch_ids = CONFIG["motion"].get("winch_ids", [1, 2, 3, 4])
    errors = []
    payload = {
        "ok": True,
        "host": host,
        "port": port,
        "winches": {},
        "distance": None,
        "imu": None,
        "errors": errors,
    }

    try:
        with EVBDriver(host, port, timeout) as evb:
            payload["ping"] = bool(_capture_read(errors, "ping", evb.ping))
            for winch_id in winch_ids:
                winch = {}
                snapshot = _capture_read(errors, f"snapshot:{winch_id}", lambda w=winch_id: evb.snapshot(w))
                delta = _capture_read(errors, f"delta:{winch_id}", lambda w=winch_id: evb.delta(w))
                bundle = _capture_read(errors, f"bundle:{winch_id}", lambda w=winch_id: evb.bundle(w))
                if snapshot is not None:
                    winch["snapshot"] = _dataclass_dict(snapshot)
                if delta is not None:
                    winch["delta"] = _dataclass_dict(delta)
                if bundle is not None:
                    winch["bundle"] = _dataclass_dict(bundle)
                payload["winches"][str(winch_id)] = winch

            distance = _capture_read(errors, "distance", evb.distance)
            imu = _capture_read(errors, "imu", evb.imu)
            if distance is not None:
                payload["distance"] = distance
            if imu is not None:
                payload["imu"] = _dataclass_dict(imu)
    except Exception as exc:
        log.error(f"/evb/live error: {exc}")
        return jsonify({"ok": False, "host": host, "port": port, "error": str(exc)}), 503

    return jsonify(payload)


@app.get("/controller/status")
def controller_status():
    gamepad_control.ensure_started()
    mapping = load_gamepad_mapping()
    return jsonify({
        "ok": True,
        "mapping": mapping,
        "controller": gamepad_control.status(),
        "inputs": GAMEPAD_INPUTS,
        "actions": GAMEPAD_ACTIONS,
        "targets": [str(winch_id) for winch_id in WINCH_IDS] + ["all"],
    })


@app.post("/controller/mapping")
def controller_mapping():
    payload = request.get_json(force=True, silent=True) or {}
    mapping = load_gamepad_mapping()
    mapping["enabled"] = bool(payload.get("enabled", mapping["enabled"]))
    mapping["rpm"] = max(0, min(4000, int(payload.get("rpm", mapping["rpm"]))))
    inputs = payload.get("inputs", {})
    if isinstance(inputs, dict):
        for input_name, action in inputs.items():
            if input_name in GAMEPAD_INPUTS and action in GAMEPAD_ACTIONS:
                mapping["inputs"][input_name] = action
    save_gamepad_mapping(mapping)
    return ok({"mapping": mapping})


@app.post("/controller/start")
def controller_start():
    if mc.get_status().get("mode") not in {"SETUP", "TEST"}:
        return err("controller only starts in SETUP or TEST mode")
    gamepad_control.start()
    return ok({"controller": gamepad_control.status()})


@app.post("/controller/target")
def controller_target():
    payload = request.get_json(force=True, silent=True) or {}
    try:
        if mc.get_status().get("mode") != "SETUP":
            return err("controller target can only change in SETUP mode")
        gamepad_control.set_target(payload.get("target", "1"))
        return ok({"controller": gamepad_control.status()})
    except Exception as exc:
        return err(str(exc))


@app.post("/controller/stop")
def controller_stop():
    gamepad_control.stop(disable_monitor=True)
    return ok({"controller": gamepad_control.status()})


@app.get("/")
def index():
    return (
        """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Supervisor Control</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
  <style>
    body {
      padding: 1.5rem;
      min-height: 100vh;
      transition: background-color 180ms ease, box-shadow 180ms ease;
    }
    body.mode-idle { background: #f5f6f8; box-shadow: inset 0 0 0 9999px rgba(108,117,125,0.03); }
    body.mode-setup { background: #eaf2ff; box-shadow: inset 0 0 0 9999px rgba(13,110,253,0.08); }
    body.mode-test { background: #eaf7ef; box-shadow: inset 0 0 0 9999px rgba(25,135,84,0.08); }
    body.mode-fault { background: #fdecef; box-shadow: inset 0 0 0 9999px rgba(220,53,69,0.10); }
    body.mode-idle #mode-banner { background: #6c757d; }
    body.mode-setup #mode-banner { background: #0d6efd; }
    body.mode-test #mode-banner { background: #198754; }
    body.mode-fault #mode-banner { background: #dc3545; }
    .card + .card { margin-top: 1rem; }
    .btn-group .btn { min-width: 90px; }
    pre { background: #111; color: #0f0; padding: 0.75rem; border-radius: 6px; }
    .invert-active .btn-check:checked + .btn,
    .invert-active .btn.active {
      background-color: var(--bs-btn-active-bg);
      border-color: var(--bs-btn-active-border-color);
      color: var(--bs-btn-active-color);
    }
    .invert-active .btn-check:focus + .btn,
    .invert-active .btn:focus {
      box-shadow: 0 0 0 0.2rem rgba(0,0,0,0.25);
    }
    label.disabled { opacity: 0.5; }
    .controller-live-active { background-color: rgba(25,135,84,0.12); }
  </style>
</head>
<body>
  <div class="container-fluid">
    <h3 class="mb-3">Supervisor Control</h3>
    <div id="mode-banner" class="text-white rounded shadow-sm px-3 py-2 mb-3">Mode: loading</div>
    <div class="row g-3">
      <div class="col-lg-4">
        <div class="card shadow-sm">
          <div class="card-header">Mode</div>
          <div class="card-body d-grid gap-2">
            <div class="btn-group invert-active" role="group" aria-label="Mode toggle">
              <input type="radio" class="btn-check" name="mode" id="mode-idle" autocomplete="off" onclick="post('/mode/idle')">
              <label class="btn btn-outline-secondary" for="mode-idle">IDLE</label>

              <input type="radio" class="btn-check" name="mode" id="mode-setup" autocomplete="off" onclick="setMode('setup')">
              <label class="btn btn-outline-primary" for="mode-setup">SETUP</label>

              <input type="radio" class="btn-check" name="mode" id="mode-test" autocomplete="off" onclick="post('/mode/test')">
              <label class="btn btn-outline-success" for="mode-test">TEST</label>
            </div>
            <button class="btn btn-outline-warning" onclick="post('/fault/clear')">Clear Fault</button>
          </div>
        </div>
        <div class="card shadow-sm">
          <div class="card-header">Stop</div>
          <div class="card-body d-grid gap-2">
            <button class="btn btn-danger" onclick="post('/stop/all', {reason:'manual emergency'})">EMERGENCY STOP</button>
            <button class="btn btn-outline-danger" onclick="post('/stop', {reason:'manual stop'})">Soft Stop (no fault)</button>
          </div>
        </div>
        <div class="card shadow-sm">
          <div class="card-header">Wireless Controller (SETUP per-winch / TEST directional)</div>
          <div class="card-body">
            <div id="controller-status" class="mb-2 small text-muted">Loading…</div>
            <div class="row g-2 mb-2">
              <div class="col-6">
                <label class="form-label">RPM</label>
                <input id="controller-rpm" type="number" class="form-control" value="250">
              </div>
              <div class="col-6 d-flex align-items-end">
                <div class="form-check">
                  <input id="controller-enabled" class="form-check-input" type="checkbox" checked onchange="toggleControllerEnabled()">
                  <label class="form-check-label" for="controller-enabled">Enable</label>
                </div>
              </div>
            </div>
            <div class="mb-2">
              <label class="form-label">Controller Target</label>
              <select id="controller-target" class="form-select" onchange="setControllerTarget(this.value)">
                <option value="1">Winch 1</option>
                <option value="2">Winch 2</option>
                <option value="3">Winch 3</option>
                <option value="4">Winch 4</option>
                <option value="all">All Winches</option>
              </select>
              <div class="form-text">SETUP only: Button 7 cycles W1→W2→W3→W4→All, Button 6 cycles back. Hold Y/A to move selected target until release.</div>
            </div>
            <div id="controller-mapping"></div>
            <div id="controller-raw" class="small text-muted mt-2"></div>
            <div class="d-grid gap-2 mt-2">
              <button class="btn btn-outline-primary" onclick="saveControllerMapping()">Save Mapping</button>
              <button class="btn btn-outline-success" onclick="post('/controller/start')">Start Controller</button>
              <button class="btn btn-outline-danger" onclick="post('/controller/stop')">Stop Controller</button>
            </div>
          </div>
        </div>
      </div>
      <div class="col-lg-4">
        <div class="card shadow-sm">
          <div class="card-header">Setup Hall Run (requires SETUP mode)</div>
          <div class="card-body">
            <div class="row g-2 mb-2">
              <div class="col-6">
                <label class="form-label">RPM</label>
                <input id="setup-rpm" type="number" class="form-control" value="200">
              </div>
              <div class="col-6">
                <label class="form-label">Max Seconds</label>
                <input id="setup-sec" type="number" step="0.1" class="form-control" value="0">
              </div>
            </div>
            <div class="btn-group w-100 mb-2 invert-active" role="group" aria-label="Setup direction">
              <input type="radio" class="btn-check" name="setup-dir" id="setup-dir-fwd" autocomplete="off" checked>
              <label class="btn btn-outline-dark" for="setup-dir-fwd">Forward</label>
              <input type="radio" class="btn-check" name="setup-dir" id="setup-dir-rev" autocomplete="off">
              <label class="btn btn-outline-dark" for="setup-dir-rev">Reverse</label>
            </div>
            <button class="btn btn-primary w-100" onclick="post('/setup/hall', {rpm:getVal('setup-rpm'), seconds:getVal('setup-sec'), direction:getSetupDir()})">Run Hall</button>
          </div>
        </div>
        <div class="card shadow-sm">
          <div class="card-header">UP Test (requires TEST mode)</div>
          <div class="card-body">
            <div class="row g-2 mb-2">
              <div class="col-6">
                <label class="form-label">RPM</label>
                <input id="up-rpm" type="number" class="form-control" value="350">
              </div>
              <div class="col-6">
                <label class="form-label">Seconds</label>
                <input id="up-sec" type="number" step="0.1" class="form-control" value="10">
              </div>
            </div>
            <button id="up-test-btn" class="btn btn-success w-100" onclick="post('/test/up', {rpm:getVal('up-rpm'), seconds:getVal('up-sec')})">Start UP Test</button>
          </div>
        </div>
      </div>
      <div class="col-lg-4">
        <div class="card shadow-sm">
          <div class="card-header">Directional Tests (TEST mode)</div>
          <div class="card-body">
            <div class="row g-2 mb-2">
              <div class="col-6">
                <label class="form-label">RPM</label>
                <input id="dir-rpm" type="number" class="form-control" value="350">
              </div>
              <div class="col-6">
                <label class="form-label">Seconds</label>
                <input id="dir-sec" type="number" step="0.1" class="form-control" value="6">
              </div>
            </div>
            <div class="btn-group w-100 invert-active" role="group" aria-label="Directional test row 1">
              <input type="radio" class="btn-check" name="dir-test" id="dir-forward" autocomplete="off" onclick="dir('forward')">
              <label class="btn btn-outline-dark" for="dir-forward">Forward</label>
              <input type="radio" class="btn-check" name="dir-test" id="dir-back" autocomplete="off" onclick="dir('back')">
              <label class="btn btn-outline-dark" for="dir-back">Back</label>
            </div>
            <div class="btn-group w-100 mt-2 invert-active" role="group" aria-label="Directional test row 2">
              <input type="radio" class="btn-check" name="dir-test" id="dir-left" autocomplete="off" onclick="dir('left')">
              <label class="btn btn-outline-dark" for="dir-left">Left</label>
              <input type="radio" class="btn-check" name="dir-test" id="dir-right" autocomplete="off" onclick="dir('right')">
              <label class="btn btn-outline-dark" for="dir-right">Right</label>
            </div>
            <div class="btn-group w-100 mt-2 invert-active" role="group" aria-label="Directional test row 3">
              <input type="radio" class="btn-check" name="dir-test" id="dir-up" autocomplete="off" onclick="dir('up')">
              <label class="btn btn-outline-dark" for="dir-up">Up</label>
              <input type="radio" class="btn-check" name="dir-test" id="dir-down" autocomplete="off" onclick="dir('down')">
              <label class="btn btn-outline-dark" for="dir-down">Down</label>
            </div>
          </div>
        </div>
        <div class="card shadow-sm mt-3">
          <div class="card-header">Status</div>
          <div class="card-body">
            <div id="status-text" class="mb-2 small text-muted">Loading…</div>
            <div id="halls-table" class="mb-2"></div>
            <div id="power-table" class="mb-2"></div>
            <div id="bundle-table" class="mb-2"></div>
            <div id="imu-box" class="mb-2"></div>
            <pre id="status-json" class="small">{}</pre>
            <button class="btn btn-outline-secondary w-100" onclick="refreshStatus()">Refresh</button>
          </div>
        </div>
        <div class="card shadow-sm mt-3">
          <div class="card-header">EVB Live</div>
          <div class="card-body">
            <div id="evb-live-text" class="mb-2 small text-muted">Not loaded</div>
            <pre id="evb-live-json" class="small">{}</pre>
            <button class="btn btn-outline-secondary w-100" onclick="refreshEvb()">Refresh EVB</button>
          </div>
        </div>
      </div>
    </div>
  </div>

<script>
async function post(url, data) {
  const opts = {method:'POST', headers:{'Content-Type':'application/json'}};
  if (data !== undefined) opts.body = JSON.stringify(data);
  const res = await fetch(url, opts);
  const js = await res.json().catch(()=>({ok:false,error:'bad json'}));
  document.getElementById('status-json').textContent = JSON.stringify(js, null, 2);
  refreshStatus();
}
async function refreshStatus(){
  const res = await fetch('/status');
  const js = await res.json().catch(()=>({ok:false,error:'bad json'}));
  renderStatus(js);
}
async function refreshEvb(){
  const res = await fetch('/evb/live');
  const js = await res.json().catch(()=>({ok:false,error:'bad json'}));
  renderEvb(js);
}
async function refreshController(){
  const res = await fetch('/controller/status');
  const js = await res.json().catch(()=>({ok:false,error:'bad json'}));
  renderController(js);
}
function getVal(id){ return parseFloat(document.getElementById(id).value) || 0; }
function dir(name){ post(`/test/dir/${name}`, {rpm:getVal('dir-rpm'), seconds:getVal('dir-sec')}); }
function getSetupDir(){ return document.getElementById('setup-dir-rev').checked ? 'reverse' : 'forward'; }
async function setMode(mode){
  await post(`/mode/${mode}`);
}
function renderStatus(js){
  document.getElementById('status-json').textContent = JSON.stringify(js, null, 2);
  if (js.ok === false) {
    document.getElementById('status-text').textContent = `Status error: ${js.error || 'unknown'}`;
    return;
  }
  setPageMode(js.mode, js.fault);
  document.getElementById('status-text').textContent = `Mode: ${js.mode} | Fault: ${js.fault || 'none'} | Last update: ${fmtTime(js.last_update)}`;

  document.getElementById('mode-idle').checked = (js.mode === 'IDLE');
  document.getElementById('mode-setup').checked = (js.mode === 'SETUP');
  document.getElementById('mode-test').checked = (js.mode === 'TEST');
  const upBtn = document.getElementById('up-test-btn');
  if (upBtn) upBtn.disabled = false;

  const halls = js.halls || {};
  let hallsHtml = '<table class="table table-sm table-bordered"><thead><tr><th>Winch</th><th>Hall</th></tr></thead><tbody>';
  for (const [k,v] of Object.entries(halls)) { hallsHtml += `<tr><td>${k}</td><td>${v}</td></tr>`; }
  hallsHtml += '</tbody></table>';
  document.getElementById('halls-table').innerHTML = '<strong>Hall</strong>' + hallsHtml;

  const power = js.power || {};
  let pHtml = '<table class="table table-sm table-bordered"><thead><tr><th>Winch</th><th>Bus (mV)</th><th>Current (mA)</th><th>Power (mW)</th></tr></thead><tbody>';
  for (const [k,v] of Object.entries(power)) { pHtml += `<tr><td>${k}</td><td>${v.bus_mv}</td><td>${v.current_ma}</td><td>${v.power_mw}</td></tr>`; }
  pHtml += '</tbody></table>';
  document.getElementById('power-table').innerHTML = '<strong>Power</strong>' + pHtml;

  const bundles = js.bundles || {};
  let bHtml = '<table class="table table-sm table-bordered"><thead><tr><th>Winch</th><th>Flags</th><th>Total</th><th>Delta</th><th>Hall</th><th>Dist(mm)</th><th>Strength</th><th>TempRaw</th><th>Age(ms)</th><th>Bus</th><th>Current</th><th>Power</th><th>CacheAge</th></tr></thead><tbody>';
  for (const [k,v] of Object.entries(bundles)) {
    bHtml += `<tr><td>${k}</td><td>${v.flags ?? ''}</td><td>${v.total_count ?? ''}</td><td>${v.delta_count ?? ''}</td><td>${v.hall_raw ?? ''}</td><td>${v.dist_mm ?? ''}</td><td>${v.strength ?? ''}</td><td>${v.temp_raw ?? ''}</td><td>${v.age_ms ?? ''}</td><td>${v.bus_mv ?? ''}</td><td>${v.current_ma ?? ''}</td><td>${v.power_mw ?? ''}</td><td>${v.cache_age_ms ?? ''}</td></tr>`;
  }
  bHtml += '</tbody></table>';
  document.getElementById('bundle-table').innerHTML = '<strong>Winch Sensors</strong>' + bHtml;

  if (js.imu) {
    const i = js.imu;
    const imuTxt = `Gyro: (${i.gyro[0].toFixed(2)}, ${i.gyro[1].toFixed(2)}, ${i.gyro[2].toFixed(2)}) | ` +
      `Accel: (${i.accel[0].toFixed(2)}, ${i.accel[1].toFixed(2)}, ${i.accel[2].toFixed(2)}) | ` +
      `Pitch: ${i.pitch.toFixed(2)} Roll: ${i.roll.toFixed(2)} Yaw: ${i.yaw.toFixed(2)} | Temp: ${i.temp_c.toFixed(1)}C | CacheAge: ${i.cache_age_ms ?? ''}`;
    document.getElementById('imu-box').innerHTML = '<strong>IMU</strong><div class="small">' + imuTxt + '</div>';
  } else {
    document.getElementById('imu-box').innerHTML = '<strong>IMU</strong><div class="small text-muted">(no data yet)</div>';
  }
}

function setPageMode(mode, fault){
  const body = document.body;
  body.classList.remove('mode-idle', 'mode-setup', 'mode-test', 'mode-fault');
  const nextMode = fault ? 'FAULT' : (mode || 'IDLE');
  body.classList.add(`mode-${nextMode.toLowerCase()}`);
  const banner = document.getElementById('mode-banner');
  if (banner) banner.textContent = `Mode: ${nextMode}${fault ? ` | Fault: ${fault}` : ''}`;
}

function renderEvb(js){
  document.getElementById('evb-live-json').textContent = JSON.stringify(js, null, 2);
  if (js.ok === false) {
    document.getElementById('evb-live-text').textContent = `EVB error: ${js.error || 'unknown'}`;
    return;
  }
  const errorCount = (js.errors || []).length;
  const winchCount = Object.keys(js.winches || {}).length;
  document.getElementById('evb-live-text').textContent =
    `Host: ${js.host}:${js.port} | Ping: ${js.ping ? 'ok' : 'fail'} | Winches: ${winchCount} | Read errors: ${errorCount}`;
}

function renderController(js){
  if (js.ok === false) {
    document.getElementById('controller-status').textContent = `Controller error: ${js.error || 'unknown'}`;
    return;
  }
  const mapping = js.mapping || {inputs:{}};
  const controller = js.controller || {};
  const inputValues = controller.input_values || {};
  const enabledInput = document.getElementById('controller-enabled');
  const rpmInput = document.getElementById('controller-rpm');
  if (enabledInput && enabledInput.dataset.initialized !== '1') {
    enabledInput.checked = !!mapping.enabled;
    enabledInput.dataset.initialized = '1';
  }
  if (rpmInput && rpmInput.dataset.initialized !== '1') {
    rpmInput.value = mapping.rpm ?? 250;
    rpmInput.dataset.initialized = '1';
  }
  const targetSelect = document.getElementById('controller-target');
  if (targetSelect && document.activeElement !== targetSelect) {
    const targetOptions = (js.targets || ['1','2','3','4','all'])
      .map(t => `<option value="${t}">${t === 'all' ? 'All Winches' : `Winch ${t}`}</option>`)
      .join('');
    if (targetSelect.innerHTML !== targetOptions) targetSelect.innerHTML = targetOptions;
    targetSelect.value = controller.selected_target || '1';
  }
  document.getElementById('controller-status').textContent =
    `Monitor: ${controller.active ? 'live' : 'off'} | Device: ${controller.controller_name || 'none'} | ` +
    `Target: ${controller.selected_target || '1'} | Input: ${(controller.last_inputs || []).join(', ') || 'none'} | Action: ${controller.last_action || 'stop'} | ` +
    `Error: ${controller.last_error || 'none'} | Detected: ${formatControllerProbe(controller.last_probe)}`;

  const editing = document.activeElement && document.activeElement.classList.contains('controller-map');
  if (!editing && document.querySelectorAll('.controller-map').length === 0) {
    const actionOptions = (js.actions || []).map(a => `<option value="${a}">${a}</option>`).join('');
    const inputs = js.inputs || [];
    let html = '<div class="table-responsive"><table class="table table-sm table-bordered mb-0"><thead><tr><th>Input</th><th>Live</th><th>Action</th></tr></thead><tbody>';
    for (const input of inputs) {
      html += `<tr data-controller-row="${input}"><td>${input}</td><td><span class="badge text-bg-secondary controller-live" data-input="${input}">0</span></td><td><select class="form-select form-select-sm controller-map" data-input="${input}">${actionOptions}</select></td></tr>`;
    }
    html += '</tbody></table></div>';
    document.getElementById('controller-mapping').innerHTML = html;
    for (const select of document.querySelectorAll('.controller-map')) {
      const input = select.dataset.input;
      select.value = (mapping.inputs || {})[input] || 'none';
    }
  }
  for (const badge of document.querySelectorAll('.controller-live')) {
    const input = badge.dataset.input;
    const value = inputValues[input] ?? 0;
    const active = !!value;
    badge.textContent = value;
    badge.className = `badge controller-live ${active ? 'text-bg-success' : 'text-bg-secondary'}`;
    const row = document.querySelector(`[data-controller-row="${input}"]`);
    if (row) row.classList.toggle('controller-live-active', active);
  }
  const axes = (controller.axes || []).join(', ');
  const buttons = (controller.buttons || []).join(', ');
  const hats = (controller.hats || []).map(h => `[${h.join(',')}]`).join(' ');
  document.getElementById('controller-raw').textContent =
    `Axes: [${axes}] | Buttons: [${buttons}] | D-Pad: [${hats}]`;
}

function formatControllerProbe(probe){
  if (!probe || probe.length === 0) return 'none';
  return probe.map(p => `${p.index}:${p.name}`).join(', ');
}

async function saveControllerMapping(){
  const inputs = {};
  for (const select of document.querySelectorAll('.controller-map')) {
    inputs[select.dataset.input] = select.value;
  }
  const payload = {
    enabled: document.getElementById('controller-enabled').checked,
    rpm: getVal('controller-rpm'),
    inputs,
  };
  await post('/controller/mapping', payload);
  refreshController();
}

async function toggleControllerEnabled(){
  await saveControllerMapping();
}

async function setControllerTarget(target){
  await post('/controller/target', {target});
  refreshController();
}

function connectSSE(){
  const es = new EventSource('/events');
  es.onmessage = (evt) => {
    try {
      const js = JSON.parse(evt.data);
      renderStatus(js);
    } catch(e) {}
  };
  es.onerror = () => {
    document.getElementById('status-text').textContent = 'Status stream disconnected';
  };
}
function fmtTime(t){ if(!t) return 'n/a'; const d=new Date(t*1000); return d.toLocaleTimeString(); }
connectSSE();
refreshEvb();
refreshController();
setInterval(refreshController, 100);
</script>

</body>
</html>
        """
    )


@app.post("/mode/idle")
def mode_idle():
    try:
        log.info("UI: mode idle")
        gamepad_control.stop()
        mc.set_mode("IDLE")
        return ok()
    except Exception as exc:
        return err(str(exc))


@app.post("/mode/setup")
def mode_setup():
    try:
        log.info("UI: mode setup")
        mc.set_mode("SETUP")
        if load_gamepad_mapping().get("enabled", True):
            gamepad_control.start()
        return ok()
    except Exception as exc:
        return err(str(exc))


@app.post("/mode/test")
def mode_test():
    try:
        log.info("UI: mode test")
        mc.set_mode("TEST")
        if load_gamepad_mapping().get("enabled", True):
            gamepad_control.start()
        return ok()
    except Exception as exc:
        return err(str(exc))


@app.post("/fault/clear")
def clear_fault():
    try:
        log.info("UI: clear fault")
        gamepad_control.stop()
        mc.clear_fault()
        return ok()
    except Exception as exc:
        return err(str(exc))


@app.post("/stop")
def stop():
    reason = request.json.get("reason", "user stop") if request.is_json else "user stop"
    log.warning(f"UI: stop ({reason})")
    gamepad_control.stop()
    mc.stop_all(reason)
    return ok({"stopped": True, "reason": reason})


@app.post("/stop/all")
def stop_all_fault():
    reason = request.json.get("reason", "emergency stop") if request.is_json else "emergency stop"
    log.warning(f"UI: emergency stop ({reason})")
    gamepad_control.disable_without_motor_command()
    mc.emergency_stop(reason)
    return ok({"stopped": True, "fault": True, "reason": reason})


@app.post("/setup/jog")
def setup_jog():
    payload = request.get_json(force=True, silent=True) or {}
    rpm = int(payload.get("rpm", 200))
    seconds = float(payload.get("seconds", 1.0))
    try:
        log.info(f"UI: setup jog rpm={rpm} sec={seconds}")
        label = mc.setup_jog(rpm=rpm, seconds=seconds)
        return ok({"job": label})
    except Exception as exc:
        return err(str(exc))


@app.post("/setup/hall")
def setup_hall():
    payload = request.get_json(force=True, silent=True) or {}
    rpm = int(payload.get("rpm", 200))
    seconds = float(payload.get("seconds", 0.0))
    direction = str(payload.get("direction", "forward"))
    try:
        log.info(f"UI: setup hall rpm={rpm} sec={seconds} dir={direction}")
        label = mc.setup_hall_run(rpm=rpm, seconds=seconds, direction=direction)

        # log.info(f"Started setup hall job: {"label"}")
        return ok({"job": label})
    except Exception as exc:
        return err(str(exc))


@app.post("/test/up")
def test_up():
    payload = request.get_json(force=True, silent=True) or {}
    rpm = int(payload.get("rpm", 350))
    seconds = float(payload.get("seconds", 10.0))
    try:
        log.info(f"UI: test up rpm={rpm} sec={seconds}")
        label = mc.test_up(rpm=rpm, seconds=seconds)
        return ok({"job": label})
    except Exception as exc:
        return err(str(exc))


@app.post("/test/dir/<name>")
def test_dir(name):
    if name not in DIRECTION_MAP:
        return err("invalid direction")
    payload = request.get_json(force=True, silent=True) or {}
    rpm = int(payload.get("rpm", 350))
    seconds = float(payload.get("seconds", 6.0))
    try:
        log.info(f"UI: test dir={name} rpm={rpm} sec={seconds}")
        label = mc.test_direction(name=name, rpm=rpm, seconds=seconds)
        return ok({"job": label})
    except Exception as exc:
        return err(str(exc))


def main():
    app.run(
        host=CONFIG["web"]["host"],
        port=CONFIG["web"]["port"],
        threaded=True,
    )


if __name__ == "__main__":
    main()
