#!/usr/bin/env python3
"""
Hall-driven motor controller:
 - Polls GET_BUNDLE (0x09) for one winch over TCP.
 - If hall < 1200: stop motor.
 - If hall >= 1200: compute pct = clamp((hall-1200)/(2200-1200), 0..1),
   rpm_cmd = max(200, pct * 1500) with ceiling 1500.
 - Sends RPM to motor over Modbus RTU (/dev/ttyUSB0).
Defaults: 50 Hz loop, adaptive backoff on device timeouts.
"""

import argparse
import socket
import time
import serial

from evb_client import EvbClient, DeviceError
from winch_dump import parse_winch_list

ESP_IP = "192.168.2.123"
ESP_PORT = 5000
TCP_TIMEOUT = 1.0

SERIAL_PORT = "/dev/ttyUSB0"
BAUD_RATE = 9600
SERIAL_TIMEOUT = 1.0  # match reference script
SERIAL_PARITY = serial.PARITY_NONE
SERIAL_STOPBITS = serial.STOPBITS_ONE
SERIAL_BYTESIZE = serial.EIGHTBITS
DEFAULT_WINCHES = [1, 2, 3, 4]  # winch ids to monitor; motor id = winch id
LOG_FRAMES = False  # toggled by CLI

HALL_THRESHOLD = 1500
HALL_CEILING = 2600  # new ceiling per latest request
RPM_MAX = 1500
RPM_MIN_ACTIVE = 150
HALL_HYST_DEFAULT = 50  # off threshold = threshold - this
RPM_DEADBAND = 10  # only resend if change exceeds this
RPM_CONSTANT = 0   # 0 = use dynamic mapping; >0 = force constant rpm

MIN_INTERVAL = 0.02
MAX_INTERVAL = 0.2
BACKOFF = 1.5
RECOVER = 0.9


# --- Modbus helpers (CRC16/MODBUS) ---
def crc16_modbus(frame: bytes) -> int:
    crc = 0xFFFF
    for b in frame:
        crc ^= b
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc & 0xFFFF


def crc_bytes(frame: bytes) -> bytes:
    v = crc16_modbus(frame)
    return bytes([v & 0xFF, (v >> 8) & 0xFF])


def build_frame(slave: int, fc: int, reg: int, value: int | None = None) -> bytes:
    frame = bytearray([slave, fc]) + reg.to_bytes(2, "big")
    if fc == 0x06 and value is not None:
        frame += value.to_bytes(2, "big")
    frame += crc_bytes(frame)
    return bytes(frame)


def send_reg(ser: serial.Serial, slave: int, reg: int, value: int):
    frame = build_frame(slave, 0x06, reg, value=value)
    spaced_string = space_hex_string(frame.hex())
    ser.write(frame)
    ser.flush()
    time.sleep(0.15)
    resp = ser.read_all()
    if LOG_FRAMES:
        if resp:
            print(f"TX: {spaced_string} | RX: {resp.hex()}")
        else:
            print(f"TX: {spaced_string} | RX: <none>")
    return frame


def compute_rpm(hall: int, threshold: int, ceiling: int, rpm_min: int, rpm_max: int) -> int:
    if hall < threshold:
        return 0
    pct = (hall - threshold) / (ceiling - threshold)
    pct = max(0.0, min(1.0, pct))
    rpm = pct * rpm_max
    return int(max(rpm_min, rpm))


## ------------------------------------- Function to format a hex string with spaces
def space_hex_string(hex_string):
    spaced_hex = ' '.join(hex_string[i:i+2] for i in range(0, len(hex_string), 2))
    return spaced_hex


def main():
    ap = argparse.ArgumentParser(description="Hall-driven motor controller")
    ap.add_argument("--host", default=ESP_IP)
    ap.add_argument("--port", type=int, default=ESP_PORT)
    ap.add_argument("--timeout", type=float, default=TCP_TIMEOUT)
    ap.add_argument("--winches", type=parse_winch_list, default=DEFAULT_WINCHES,
                    help="comma-separated winch ids to drive (motors assumed same ids)")
    ap.add_argument("--serial", default=SERIAL_PORT)
    ap.add_argument("--interval", type=float, default=MIN_INTERVAL, help="initial interval (s)")
    ap.add_argument("--min-interval", type=float, default=MIN_INTERVAL)
    ap.add_argument("--max-interval", type=float, default=MAX_INTERVAL)
    ap.add_argument("--backoff", type=float, default=BACKOFF)
    ap.add_argument("--recover", type=float, default=RECOVER)
    ap.add_argument("--forward", action="store_true", help="run motor forward (default)")
    ap.add_argument("--reverse", action="store_true", help="run motor reverse")
    ap.add_argument("--wind-in", action="store_true", help="alias for reverse (wind in)")
    ap.add_argument("--log-frames", action="store_true", help="print Modbus TX/RX frames")
    ap.add_argument("--hall-threshold", type=int, default=HALL_THRESHOLD, help="hall value where motor begins")
    ap.add_argument("--hall-off", type=int, default=None, help="hall value where motor stops (defaults to threshold-100)")
    ap.add_argument("--hall-ceiling", type=int, default=HALL_CEILING, help="hall value mapped to max RPM")
    ap.add_argument("--rpm-max", type=int, default=RPM_MAX, help="max RPM command")
    ap.add_argument("--rpm-min-active", type=int, default=RPM_MIN_ACTIVE, help="minimum RPM once active")
    ap.add_argument("--rpm-deadband", type=int, default=RPM_DEADBAND,
                    help="only resend RPM if change >= this value")
    ap.add_argument("--rpm-constant", type=int, default=RPM_CONSTANT,
                    help="0 = dynamic mapping; >0 = force constant RPM when active")
    args = ap.parse_args()

    global LOG_FRAMES
    direction_forward = True
    if args.reverse or args.wind_in:
        direction_forward = False
    LOG_FRAMES = bool(args.log_frames)
    hall_threshold = args.hall_threshold
    hall_off = args.hall_off if args.hall_off is not None else args.hall_threshold - HALL_HYST_DEFAULT
    if hall_off >= hall_threshold:
        hall_off = hall_threshold - 1
    hall_ceiling = args.hall_ceiling
    rpm_max = args.rpm_max
    rpm_min_active = args.rpm_min_active
    rpm_deadband = max(0, args.rpm_deadband)
    rpm_constant = args.rpm_constant

    ser = serial.Serial(
        port=args.serial,
        baudrate=BAUD_RATE,
        timeout=SERIAL_TIMEOUT,
        parity=SERIAL_PARITY,
        stopbits=SERIAL_STOPBITS,
        bytesize=SERIAL_BYTESIZE,
    )
    interval = max(args.min_interval, args.interval)

    # per-winch state
    active = {w: False for w in args.winches}
    last_rpm_sent = {w: None for w in args.winches}

    dir_str = "forward" if direction_forward else "reverse (wind-in)"
    print(f"Starting hall control for winches {args.winches}, motor ids assumed equal, target interval {interval*1000:.1f} ms "
          f"[on>={hall_threshold}, off<={hall_off}, dir={dir_str}]")
    try:
        with EvbClient(args.host, args.port, args.timeout) as cli:
            while True:
                loop_start = time.time()
                rows = []
                for winch_id in args.winches:
                    try:
                        resp_type, payload = cli.send(0x09, bytes([winch_id & 0xFF]))
                        total_count = int.from_bytes(payload[2:6], "little", signed=True)
                        delta_count = int.from_bytes(payload[6:10], "little", signed=True)
                        hall = int.from_bytes(payload[10:12], "little", signed=False)
                        bus_mv = int.from_bytes(payload[20:22], "little", signed=False)
                        current_ma = int.from_bytes(payload[22:24], "little", signed=True)
                        power_mw = int.from_bytes(payload[24:28], "little", signed=False)
                    except DeviceError as exc:
                        print(f"bundle error winch {winch_id}: {exc}; backing off")
                        interval = min(args.max_interval, interval * args.backoff)
                        time.sleep(interval)
                        continue
                    except (socket.timeout, TimeoutError) as exc:
                        print(f"socket timeout winch {winch_id}: {exc}; backing off")
                        interval = min(args.max_interval, interval * args.backoff)
                        time.sleep(interval)
                        continue

                    # hysteresis state machine
                    if not active[winch_id] and hall >= hall_threshold:
                        active[winch_id] = True
                    elif active[winch_id] and hall <= hall_off:
                        active[winch_id] = False

                    motor_id = winch_id  # 1:1 mapping

                    if not active[winch_id]:
                        if last_rpm_sent[winch_id] != 0:
                            send_reg(ser, motor_id, 0x8000, 0x0802)  # stop
                            last_rpm_sent[winch_id] = 0
                            print(f"IDLE winch={winch_id} hall={hall} stop sent")
                    else:
                        rpm_cmd = rpm_constant if rpm_constant > 0 else compute_rpm(
                            hall, hall_threshold, hall_ceiling, rpm_min_active, rpm_max
                        )
                        if last_rpm_sent[winch_id] is None or abs(rpm_cmd - last_rpm_sent[winch_id]) >= rpm_deadband:
                            raw = ((rpm_cmd & 0xFF) << 8) | (rpm_cmd >> 8)
                            send_reg(ser, motor_id, 0x8005, raw)
                            send_reg(ser, motor_id, 0x8000, 0x0902 if direction_forward else 0x0B02)
                            last_rpm_sent[winch_id] = rpm_cmd
                    rows.append({
                        "winch": winch_id,
                        "hall": hall,
                        "total": total_count,
                        "delta": delta_count,
                        "active": active[winch_id],
                        "rpm": last_rpm_sent[winch_id] if active[winch_id] else 0,
                        "bus_mv": bus_mv,
                        "current_ma": current_ma,
                        "power_mw": power_mw,
                    })
                # per-iteration summary
                print("winches=[")
                for r in rows:
                    print(f"  {{winch:{r['winch']}, hall:{r['hall']}, active:{r['active']}, rpm:{r['rpm']}, "
                          f"total:{r['total']}, delta:{r['delta']}, "
                          f"bus_mv:{r['bus_mv']}, current_ma:{r['current_ma']}, power_mw:{r['power_mw']}}},")
                print("]")
                # adaptive timing
                interval = max(args.min_interval, min(args.max_interval, interval * args.recover))
                sleep_for = max(0.0, interval - (time.time() - loop_start))
                time.sleep(sleep_for)
    except KeyboardInterrupt:
        print("\nStopping motor…")
    finally:
        try:
            for mid in active.keys():
                send_reg(ser, mid, 0x8000, 0x0802)
        except Exception:
            pass
        ser.close()


if __name__ == "__main__":
    raise SystemExit(main())
