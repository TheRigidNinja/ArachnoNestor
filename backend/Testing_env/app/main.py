#!/usr/bin/env python3
import argparse
import sys
import threading
from pathlib import Path

# ensure repo root on sys.path when running from app/
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from config.settings import load_config
from motor.motion_controller import get_controller

# ——————————————— IMU via binary command 0x0A ————————————————
CONFIG = load_config()
ESP_IP = CONFIG["evb"]["host"]
ESP_PORT = CONFIG["evb"]["port"]
TIMEOUT_SEC = CONFIG["evb"]["timeout"]
SAMPLE_HZ = 50.0
MIN_INTERVAL = 0.02
MAX_INTERVAL = 0.2
BACKOFF = 1.5
RECOVER = 0.9


# ——————————————— MOTOR CONTROLLER ————————————————
SERIAL_PORT = CONFIG["motion"]["serial_port"]
BAUD_RATE = CONFIG["motion"]["baud_rate"]
TIMEOUT = 1

# ——————————————— MAIN LOOP ————————————————
def main(argv=None):
    argv = list(argv) if argv is not None else sys.argv[1:]

    ap = argparse.ArgumentParser(description="Web UI + supervisor + IMU balance loop")
    ap.add_argument("--host", default=ESP_IP, help="ESP32 IMU host")
    ap.add_argument("--port", type=int, default=ESP_PORT, help="ESP32 IMU port")
    ap.add_argument("--timeout", type=float, default=TIMEOUT_SEC, help="ESP32 IMU socket timeout")
    ap.add_argument("--sample-hz", type=float, default=SAMPLE_HZ, help="control loop rate (Hz)")
    ap.add_argument("--min-interval", type=float, default=MIN_INTERVAL, help="minimum loop interval (s)")
    ap.add_argument("--max-interval", type=float, default=MAX_INTERVAL, help="maximum loop interval (s)")
    ap.add_argument("--backoff", type=float, default=BACKOFF, help="multiplier on device timeout")
    ap.add_argument("--recover", type=float, default=RECOVER, help="multiplier to speed up after success")
    ap.add_argument("--base-rpm", type=float, default=1000.0, help="baseline RPM before PID correction")
    ap.add_argument("--no-motors", action="store_true", help="run loop without commanding motors")
    ap.add_argument("--serial", default=SERIAL_PORT, help="RS485 serial port for motors")
    args = ap.parse_args(argv)

    controller = get_controller()

    # Balance loop runs in background; web UI runs in main thread.
    def _balance():
        controller.run_balance_loop(
            base_rpm=args.base_rpm,
            sample_hz=args.sample_hz,
            min_interval=args.min_interval,
            max_interval=args.max_interval,
            backoff=args.backoff,
            recover=args.recover,
            no_motors=args.no_motors,
            host=args.host,
            port=args.port,
            timeout=args.timeout,
        )

    bal_thread = threading.Thread(target=_balance, daemon=True)
    bal_thread.start()

    from app.web_control import main as web_main
    return web_main() or 0

if __name__ == "__main__":
    raise SystemExit(main())
