#!/usr/bin/env python3
import sys
import time
from pathlib import Path

import serial

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from config.settings import load_config
from drivers.modbus_motor import calculate_crc


def read_register(ser, slave_id, register):
    frame = bytearray([slave_id, 0x03])
    frame.extend(register.to_bytes(2, "big"))
    frame.extend((1).to_bytes(2, "big"))
    frame.extend(calculate_crc(frame))

    ser.reset_input_buffer()
    ser.write(frame)
    ser.flush()
    return ser.read(7)


def main():
    cfg = load_config()
    port = cfg["motion"]["serial_port"]
    baud = 9600
    register = 0x8005

    print(f"Scanning motor controllers 1-4 on {port} @ {baud}")

    with serial.Serial(port=port, baudrate=baud, timeout=1.0) as ser:
        time.sleep(0.2)
        for slave_id in range(1, 5):
            response = read_register(ser, slave_id, register)
            if response:
                print(f"Controller {slave_id}: ONLINE  {response.hex()}")
            else:
                print(f"Controller {slave_id}: offline")
            time.sleep(0.15)


if __name__ == "__main__":
    main()
