#!/usr/bin/env python3
import json
import time
from contextlib import contextmanager
from dataclasses import dataclass

from logutil.logger import get_logger
from config.settings import load_config
from tcp.line_client import LineClient

CONFIG = load_config()
log = get_logger("drivers.evb_json_client")
ESP_IP = CONFIG["evb"]["host"]
ESP_PORT = CONFIG["evb"]["port"]
CONNECTION_TIMEOUT = CONFIG["evb"]["timeout"]


@dataclass
class PIDConfig:
    kp: float = 0.5
    ki: float = 0.01
    kd: float = 0.1
    integral_max: float = 100.0
    output_max: float = 100.0


class ESP32MotorController:
    def __init__(self, pulses_per_meter=1000):
        self.client = LineClient(ESP_IP, ESP_PORT, CONNECTION_TIMEOUT, nonblocking=True)
        self.pulses_per_meter = pulses_per_meter

    def connect(self):
        try:
            self.client.connect()
            log.info(f"Connected to {ESP_IP}:{ESP_PORT}")
            return True
        except Exception as e:
            log.error(f"Connection failed: {e}")
            return False

    def _ensure_connection(self):
        if self.client.sock is None:
            if not self.connect():
                raise RuntimeError("Unable to connect to ESP32")

    def send_command(self, command: dict):
        self._ensure_connection()
        payload = json.dumps(command) + "\n"
        self.client.send(payload.encode())
        return self._recv_response()

    def _recv_response(self, timeout=2.0):
        line = self.client.recv_line(timeout=timeout)
        txt = line.decode().strip()
        try:
            return json.loads(txt)
        except Exception:
            return txt

    def close(self):
        self.client.close()

    @contextmanager
    def connection(self):
        try:
            self.connect()
            yield self
        finally:
            self.close()

    # -- existing methods omitted for brevity --

    def move_to_target(self, motor_id: int, forward: bool, pwm_limit: int, target_pulses: int, timeout: float = 10.0, tolerance: int = 2) -> bool:
        """
        Send: {
          "motors":[
            {
              "id": motor_id,
              "enable": True,
              "direction":"forward"/"reverse",
              "pwm": pwm_limit,       # max % speed
              "target": target_pulses # desired pulse count
            }
          ]
        }
        """
        cmd = {
            "motors": [
                {
                    "id": motor_id,
                    "enable": True,
                    "direction": "forward" if forward else "reverse",
                    "pwm": pwm_limit,
                    "target": target_pulses,
                }
            ]
        }

        self.send_command(cmd)

        # 2) poll until pulses are within tolerance
        start = time.time()
        while time.time() - start < timeout:
            p = self.get_pulses(motor_id)

            if abs(p - target_pulses) <= tolerance:
                return True
            time.sleep(0.01)

        return False

    def rapid_flips_test(
        self,
        motor_id: int = 0,
        step_pulses: int = 100,
        cycles: int = 10,
        pwm_limit: int = 20,
        settle_delay: float = 0.5,
    ):
        """
        Flip direction every step_pulses for cycles times,
        logging the final error each time.
        """
        # tune your PID once
        pid = PIDConfig(kp=1.0, ki=0.08, kd=0.2, integral_max=20.0, output_max=100.0)
        log.info(f"Setting PID: {pid}")
        self.send_command({"motors": [{"id": motor_id, "pid": vars(pid)}]})

        for i in range(cycles):
            start = self.get_pulses(motor_id)
            forward = i % 2 == 0
            target = start + (step_pulses if forward else -step_pulses)

            dir_str = "forward" if forward else "reverse"
            log.info(f"Cycle {i+1}/{cycles}: {dir_str} {step_pulses} pulses -> {target}")
            self.move_to_target(motor_id, forward, pwm_limit, target)
            time.sleep(settle_delay)

            actual = self.get_pulses(motor_id)
            error = actual - target
            log.info(f"Arrived at {actual}   error = {error:+d} pulses")

        log.info("Rapid-flip test complete.")

    def get_pulses(self, motor_id=0):
        cmd = {"motors": [{"id": motor_id, "pulses": True}]}
        resp = self.send_command(cmd)
        return resp["pulses"][0]


def main():
    with ESP32MotorController().connection() as ctrl:
        # ctrl.rapid_flips_test()
        # ctrl.rapid_flips_test(motor_id=0, step_pulses=650, cycles=4, pwm_limit=10)
        ctrl.move_to_target(motor_id=2, forward=False, pwm_limit=0, target_pulses=-500)
        time.sleep(10)


if __name__ == "__main__":
    main()

    # -635
