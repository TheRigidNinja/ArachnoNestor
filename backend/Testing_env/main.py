#!/usr/bin/env python3
import socket, json, time, serial
from dataclasses import dataclass
from contextlib import contextmanager
import crcmod

# ——————————————— IMU CLIENT ————————————————
ESP_IP      = "192.168.2.123"
ESP_PORT    = 5000
TIMEOUT_SEC = 5.0

@dataclass
class IMUReading:
    seq:      int
    ts:       int
    ver:      str
    errs:     int
    yaw:      float
    pitch:    float
    roll:     float
    accel:    list[float]
    gyro:     list[float]
    mag:      list[float]
    pressure: float
    temp:     float
    vbatt:    float
    heap:     int

class ESP32IMUClient:
    def __init__(self, host=ESP_IP, port=ESP_PORT, timeout=TIMEOUT_SEC):
        self.addr = (host, port)
        self.timeout = timeout
        self.sock = None
        self.fd = None

    def connect(self):
        self.sock = socket.create_connection(self.addr, self.timeout)
        self.fd   = self.sock.makefile("r")
        print(f"✅ IMU connected to {self.addr}")

    def close(self):
        if self.fd:   self.fd.close()
        if self.sock: self.sock.close()
        print("🔌 IMU disconnected")

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *args):
        self.close()

    def stream(self):
        while True:
            line = self.fd.readline()
            if not line:
                raise ConnectionError("ESP32 closed connection")
            d = json.loads(line)
            yield IMUReading(
                seq      = int(d["seq"]),
                ts       = int(d["ts"]),
                ver      = d["ver"],
                errs     = int(d["errs"]),
                yaw      = float(d["yaw"]),
                pitch    = float(d["pitch"]),
                roll     = float(d["roll"]),
                accel    = d["accel"],
                gyro     = d["gyro"],
                mag      = d["mag"],
                pressure = float(d["pressure"]),
                temp     = float(d["temp"]),
                vbatt    = float(d["vbatt"]),
                heap     = int(d["heap"])
            )

# ——————————————— MOTOR CONTROLLER ————————————————
SERIAL_PORT = '/dev/ttyUSB0'
BAUD_RATE   = 9600
TIMEOUT     = 1

# list your four Modbus addresses here:
DEVICE_ADDRESSES = [1, 2, 3, 4]

# build CRC-16 (Modbus)
crc16 = crcmod.mkCrcFun(0x18005, rev=True, initCrc=0xFFFF, xorOut=0x0000)

def calculate_crc(data: bytes) -> bytes:
    v = crc16(data)
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
def main():
    # 1) Open RS-485 port
    ser = serial.Serial(port=SERIAL_PORT, baudrate=BAUD_RATE, timeout=TIMEOUT)

    # 2) Build PID for roll stabilization
    pid_roll = PID(kp=20.0, ki=0.1, kd=5.0, mn=-1000, mx=1000)

    # 3) Connect IMU stream
    with ESP32IMUClient() as imu_client:
        stream = imu_client.stream()
        last = time.time()
        print("↔️  Starting balance loop… Ctrl-C to exit")
        try:
            while True:
                now = time.time()
                dt = now - last
                last = now

                reading = next(stream)
                roll = reading.roll  # degrees

                # PID output: +ve → add speed, –ve → subtract
                correction = pid_roll.update(roll, dt)

                # base speed + correction
                base = 1000
                rpm_target = base + correction

                # send to all four devices
                for dev in DEVICE_ADDRESSES:
                    write_rpm(ser, dev, rpm_target)
                    start_motor(ser, dev, forward=True)

                print(f"roll={roll:+5.2f}°  corr={correction:+6.1f} → RPM={rpm_target:.0f}")
                time.sleep(1.0/SAMPLE_HZ)

        except KeyboardInterrupt:
            print("\n🛑  Shutting down…")
        finally:
            # natural stop all motors
            for dev in DEVICE_ADDRESSES:
                stop_motor(ser, dev, brake=False)
            ser.close()

if __name__ == "__main__":
    main()
