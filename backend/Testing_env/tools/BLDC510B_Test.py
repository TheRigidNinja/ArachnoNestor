#!/usr/bin/env python3
from drivers.imu_driver import ESP32IMUClient

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
