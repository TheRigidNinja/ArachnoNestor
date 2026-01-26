#!/usr/bin/env python3
"""
Poll winch and IMU data over the ESP32-EVB TCP protocol and print results.

Supported commands (per latest EVB firmware):
  - 0x04 GET_SNAPSHOT   -> total_count, hall_raw (per winch)
  - 0x05 GET_DELTA      -> delta_count (per winch)
  - 0x07 GET_DISTANCE   -> dist/strength/temp/age (global)
  - 0x08 GET_POWER      -> bus/current/power (per winch) — still available, but bundle includes these
  - 0x09 GET_BUNDLE     -> flags + encoder + hall + distance + power (per winch)
  - 0x0A GET_IMU        -> gyro, accel, temp, pitch/roll/yaw (global)
"""

import argparse
import time
from typing import List

from drivers.evb_driver import EVBDriver, DeviceError, get_bundle, get_distance, get_imu


def poll_once(cli, winches: List[int]):
    rows = []
    for w in winches:
        bundle = get_bundle(cli, w)
        rows.append(bundle.__dict__)
    return rows


def format_rows(rows):
    lines = ["winches=["]
    for r in rows:
        lines.append(
            "  {"
            f"winch:{r['winch']}, flags:0x{r['flags']:02X}, "
            f"total:{r['total_count']}, delta:{r['delta_count']}, hall:{r['hall_raw']}, "
            f"dist_mm:{r['dist_mm']}, strength:{r['strength']}, temp_raw:{r['temp_raw']}, age_ms:{r['age_ms']}, "
            f"bus_mv:{r['bus_mv']}, current_ma:{r['current_ma']}, power_mw:{r['power_mw']}"
            "},"
        )
    lines.append("]")
    return "\n".join(lines)


def format_imu(imu) -> str:
    return (
        f"IMU gyro=({imu.gyro[0]:.3f},{imu.gyro[1]:.3f},{imu.gyro[2]:.3f}) "
        f"accel=({imu.accel[0]:.3f},{imu.accel[1]:.3f},{imu.accel[2]:.3f}) "
        f"temp={imu.temp_c:.2f}C pitch={imu.pitch:.2f} roll={imu.roll:.2f} yaw={imu.yaw:.2f}"
    )


def parse_winch_list(text: str) -> List[int]:
    """Parse comma-separated ints like '0,1,2,3'."""
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if not parts:
        raise argparse.ArgumentTypeError("provide at least one winch id")
    vals = []
    for p in parts:
        try:
            v = int(p)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"invalid winch id '{p}'") from exc
        if not (0 <= v <= 255):
            raise argparse.ArgumentTypeError("winch id must be 0-255")
        vals.append(v)
    return vals


def main():
    ap = argparse.ArgumentParser(description="Poll all winch data via ESP32-EVB")
    from config.settings import load_config
    cfg = load_config()
    ap.add_argument("--host", default=cfg["evb"]["host"])
    ap.add_argument("--port", type=int, default=cfg["evb"]["port"])
    ap.add_argument("--timeout", type=float, default=1.0)
    ap.add_argument("--winches", type=parse_winch_list, default=[1, 2, 3, 4],
                    help="comma-separated winch ids, e.g. 1,2,3,4")
    ap.add_argument("--interval", type=float, default=0.02, help="initial seconds between polls (default 20 ms)")
    ap.add_argument("--min-interval", type=float, default=0.02, help="floor for adaptive interval")
    ap.add_argument("--max-interval", type=float, default=0.2, help="ceiling for adaptive interval")
    ap.add_argument("--backoff", type=float, default=1.5, help="multiplier on timeout/device error")
    ap.add_argument("--recover", type=float, default=0.9, help="multiplier to speed up after success")
    ap.add_argument("--count", type=int, default=0,
                    help="number of iterations (0 = run forever)")
    ap.add_argument("--no-imu", action="store_true", help="skip IMU read (0x0A)")
    ap.add_argument("--no-distance", action="store_true", help="skip global distance read (0x07)")
    args = ap.parse_args()

    iteration = 0
    interval = args.interval
    try:
        with EVBDriver(args.host, args.port, args.timeout) as cli:
            while True:
                iteration += 1
                had_error = False
                try:
                    rows = poll_once(cli, args.winches)
                except DeviceError as exc:
                    print(f"bundle error: {exc}; backing off 0.2s", flush=True)
                    had_error = True
                    time.sleep(0.2)
                    interval = min(args.max_interval, interval * args.backoff)
                    continue
                except Exception as exc:
                    print(f"bundle read failed: {exc}; reconnecting…", flush=True)
                    break
                if not args.no_distance:
                    try:
                        dist = get_distance(cli)
                    except DeviceError as exc:
                        print(f"distance error: {exc}; backing off 0.2s", flush=True)
                        time.sleep(0.2)
                        interval = min(args.max_interval, interval * args.backoff)
                        dist = None
                        had_error = True
                    except Exception as exc:
                        dist = None
                        print(f"distance read failed: {exc}")
                else:
                    dist = None
                if not args.no_imu:
                    try:
                        imu = get_imu(cli)
                    except DeviceError as exc:
                        print(f"IMU error: {exc}; backing off 0.2s", flush=True)
                        time.sleep(0.2)
                        interval = min(args.max_interval, interval * args.backoff)
                        imu = None
                        had_error = True
                    except Exception as exc:
                        imu = None
                        print(f"IMU read failed: {exc}")
                else:
                    imu = None

                # Pretty print as arrays/objects
                print(f"[{time.strftime('%H:%M:%S')}] poll #{iteration}")
                print("winches=[")
                for r in rows:
                    print(
                        f"  {{winch:{r['winch']}, flags:0x{r['flags']:02X}, total:{r['total_count']}, delta:{r['delta_count']}, "
                        f"hall:{r['hall_raw']}, dist_mm:{r['dist_mm']}, strength:{r['strength']}, temp_raw:{r['temp_raw']}, age_ms:{r['age_ms']}, "
                        f"bus_mv:{r['bus_mv']}, current_ma:{r['current_ma']}, power_mw:{r['power_mw']}}},"
                    )
                print("]")
                if dist:
                    print(
                        f"distance={{ok:{dist['ok']}, dist_mm:{dist['dist_mm']}, strength:{dist['strength']}, temp_raw:{dist['temp_raw']}, age_ms:{dist['age_ms']}}}"
                    )
                if imu:
                    print("imu={")
                    print(f"  gyro: [{imu.gyro[0]:.3f}, {imu.gyro[1]:.3f}, {imu.gyro[2]:.3f}],")
                    print(f"  accel:[{imu.accel[0]:.3f}, {imu.accel[1]:.3f}, {imu.accel[2]:.3f}],")
                    print(f"  temp_c:{imu.temp_c:.2f}, pitch:{imu.pitch:.2f}, roll:{imu.roll:.2f}, yaw:{imu.yaw:.2f}")
                    print("}")

                if 0 < args.count <= iteration:
                    break
                # adaptive interval
                if not had_error:
                    interval = max(args.min_interval, interval * args.recover)
                time.sleep(interval)
    except KeyboardInterrupt:
        print("stopped by user")
    except Exception as exc:
        print(f"error: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
