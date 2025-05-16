#!/usr/bin/env python3
import socket
import json
from dataclasses import dataclass
from contextlib import contextmanager

ESP_IP      = "192.168.2.123"
ESP_PORT    = 5000
TIMEOUT_SEC = 5.0

@dataclass
class IMUReading:
    seq:      int
    ts:       int     # μs since ESP boot
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
        self.addr    = (host, port)
        self.timeout = timeout
        self.sock    = None
        self.fd      = None

    def connect(self):
        self.sock = socket.create_connection(self.addr, self.timeout)
        self.fd   = self.sock.makefile("r")
        print(f"✅ Connected to {self.addr[0]}:{self.addr[1]}")

    def close(self):
        if self.fd:
            self.fd.close()
            self.fd = None
        if self.sock:
            self.sock.close()
            self.sock = None
        print("🔌 Disconnected")

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def stream(self):
        """Yield IMUReading for each JSON line."""
        while True:
            line = self.fd.readline()
            if not line:
                raise ConnectionError("ESP32 closed connection")
            try:
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
            except (ValueError, KeyError) as e:
                print(f"⚠️ JSON parse error: {e}")
                continue

def main():
    with ESP32IMUClient() as client:
        print("Streaming IMU + Baro + Health data (Ctrl-C to quit)…")
        try:
            for reading in client.stream():
                print(f"[{reading.seq:05d}] ts={reading.ts}us  ver={reading.ver}  errs={reading.errs}")
                print(f"  YPR: {reading.yaw:.2f}, {reading.pitch:.2f}, {reading.roll:.2f}")
                print(f"  ACC: {reading.accel}  GYR: {reading.gyro}  MAG: {reading.mag}")
                print(f"  PRESS: {reading.pressure:.0f}  TEMP: {reading.temp:.2f}V  VBATT: {reading.vbatt:.2f}V")
                print(f"  HEAP: {reading.heap} bytes\n")
        except KeyboardInterrupt:
            print("\n🛑 Stopped by user")

if __name__ == "__main__":
    main()
