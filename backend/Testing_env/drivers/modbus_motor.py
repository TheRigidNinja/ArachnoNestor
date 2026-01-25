"""Modbus RTU motor helpers."""

import time


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
    frame = bytearray([slave, fc]) + addr.to_bytes(2, "big")
    if fc == 0x06 and value is not None:       # write single register
        frame += value.to_bytes(2, "big")
    elif fc == 0x03 and count is not None:     # read holding registers
        frame += count.to_bytes(2, "big")
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
