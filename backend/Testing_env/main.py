#!/usr/bin/env python3
import argparse
import socket, json, time, serial, sys, struct
from dataclasses import dataclass
from contextlib import contextmanager

from evb_client import EvbClient, DeviceError  # persistent binary TCP client

# ——————————————— IMU via binary command 0x0A ————————————————
ESP_IP      = "192.168.2.123"
ESP_PORT    = 5000
TIMEOUT_SEC = 5.0
SAMPLE_HZ   = 50.0  # default loop rate (~20 ms period)
MIN_INTERVAL = 0.02
MAX_INTERVAL = 0.2
BACKOFF = 1.5
RECOVER = 0.9

def get_imu_binary(cli: EvbClient) -> dict:
    """
    Uses TCP command 0x0A (GET_IMU).
    Payload layout (40B): [gyro 3x f32][accel 3x f32][temp f32][pitch f32][roll f32][yaw f32]
    """
    resp_type, payload = cli.send(0x0A, b"")
    if resp_type != 0x0A or len(payload) != 40:
        raise RuntimeError(f"IMU bad response type=0x{resp_type:02X} len={len(payload)}")
    vals = struct.unpack("<10f", payload)
    return {
        "gyro": vals[0:3],
        "accel": vals[3:6],
        "temp_c": vals[6],
        "pitch": vals[7],
        "roll": vals[8],
        "yaw": vals[9],
    }

# ——————————————— MOTOR CONTROLLER ————————————————
SERIAL_PORT = '/dev/ttyUSB0'
BAUD_RATE   = 9600
TIMEOUT     = 1

# list your four Modbus addresses here:
DEVICE_ADDRESSES = [1, 2, 3, 4]

# build CRC-16 (Modbus) inline to avoid external dependency
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

def calculate_crc(data: bytes) -> bytes:
    v = crc16_modbus(data)
    return bytes([v & 0xFF, (v >> 8) & 0xFF])

def send_modbus_command(ser, slave: int, fc: int, addr: int, value=None, count=None):
    frame = bytearray([slave, fc]) + addr.to_bytes(2,'big')
    if fc == 0x06 and value is not None:       # write single register
        frame += value.to_bytes(2,'big')
    elif fc == 0x03 and count is not None:     # read holding registers
        frame += count.to_bytes(2,'big')
    frame += calculate_crc(frame)
    ser.write(frame)
    time.sleep(0.05)
    return ser.read_all()

def write_rpm(ser, slave: int, rpm: int):
    rpm = max(0, min(4000, int(rpm)))
    # motor expects LE in register 0x8005
    raw = ((rpm & 0xFF) << 8) | ((rpm >> 8) & 0xFF)
    return send_modbus_command(ser, slave, 0x06, 0x8005, value=raw)

def start_motor(ser, slave: int, forward=True):
    code = 0x0902 if forward else 0x0B02
    return send_modbus_command(ser, slave, 0x06, 0x8000, value=code)

def stop_motor(ser, slave: int, brake=False):
    code = 0x0D02 if brake else 0x0802
    return send_modbus_command(ser, slave, 0x06, 0x8000, value=code)

# ——————————————— SIMPLE PID ————————————————
class PID:
    def __init__(self, kp, ki, kd, mn, mx):
        self.kp, self.ki, self.kd = kp, ki, kd
        self.mn, self.mx = mn, mx
        self.setpoint = 0.0
        self._int = 0.0
        self._last = None

    def update(self, measure, dt):
        err = self.setpoint - measure
        self._int += err * dt
        der = 0 if self._last is None else (err - self._last)/dt
        self._last = err
        u = self.kp*err + self.ki*self._int + self.kd*der
        return max(self.mn, min(self.mx, u))

# ——————————————— MAIN LOOP ————————————————
def main(argv=None):
    ap = argparse.ArgumentParser(description="IMU-driven motor balance loop (testing env)")
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

    # 1) Open RS-485 port (unless disabled)
    ser = None
    if not args.no_motors:
        ser = serial.Serial(port=args.serial, baudrate=BAUD_RATE, timeout=TIMEOUT)

    # 2) Build PID for roll stabilization
    pid_roll = PID(kp=20.0, ki=0.1, kd=5.0, mn=-1000, mx=1000)

    # 3) Connect IMU stream
    try:
        last = time.time()
        print("↔️  Starting balance loop (binary IMU 0x0A)… Ctrl-C to exit")
        interval = max(args.min_interval, 1.0 / args.sample_hz if args.sample_hz > 0 else MIN_INTERVAL)
        while True:
            # Establish or re-establish the TCP session
            try:
                with EvbClient(args.host, args.port, args.timeout) as cli:
                    while True:
                        loop_start = time.time()
                        dt = loop_start - last
                        last = loop_start

                        try:
                            imu = get_imu_binary(cli)
                        except DeviceError as exc:
                            if exc.code == 2:  # compact timeout, keep connection and backoff
                                print(f"IMU device timeout (code=2); backing off…", file=sys.stderr)
                                interval = min(args.max_interval, interval * args.backoff)
                                time.sleep(interval)
                                continue
                            print(f"IMU device error: {exc}; reconnecting…", file=sys.stderr)
                            break  # exit inner loop to reconnect
                        except Exception as exc:
                            print(f"IMU read failed ({exc}); reconnecting…", file=sys.stderr)
                            break  # exit inner loop to reconnect

                        roll = imu["roll"]  # degrees

                        correction = pid_roll.update(roll, dt)
                        rpm_target = args.base_rpm + correction

                        if not args.no_motors:
                            for dev in DEVICE_ADDRESSES:
                                write_rpm(ser, dev, rpm_target)
                                start_motor(ser, dev, forward=True)

                        print(
                            f"roll={roll:+6.2f}° pitch={imu['pitch']:+6.2f} yaw={imu['yaw']:+6.2f} "
                            f"gyro=({imu['gyro'][0]:+.2f},{imu['gyro'][1]:+.2f},{imu['gyro'][2]:+.2f}) "
                            f"corr={correction:+7.1f} → RPM={rpm_target:.0f}"
                        )
                        # maintain adaptive loop timing
                        interval = max(args.min_interval, min(args.max_interval, interval * args.recover))
                        sleep_for = max(0.0, interval - (time.time() - loop_start))
                        time.sleep(sleep_for)

            except (socket.timeout, TimeoutError):
                print(f"IMU connection timed out to {args.host}:{args.port} (timeout={args.timeout}s); retrying…", file=sys.stderr)
            except ConnectionRefusedError:
                print(f"IMU connection refused at {args.host}:{args.port}; retrying…", file=sys.stderr)
            except OSError as exc:
                print(f"IMU connection failed: {exc}; retrying…", file=sys.stderr)

            # small backoff before reconnect attempt
            time.sleep(0.2)

    except KeyboardInterrupt:
        print("\n🛑  Shutting down…")
    finally:
        if ser is not None:
            for dev in DEVICE_ADDRESSES:
                stop_motor(ser, dev, brake=False)
            ser.close()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
