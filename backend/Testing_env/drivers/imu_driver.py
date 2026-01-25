#!/usr/bin/env python3
"""JSON-line IMU stream over TCP (legacy ESP32 format)."""

from __future__ import annotations

import json
import socket
from dataclasses import dataclass

from config.settings import load_config
from logutil.logger import get_logger

CONFIG = load_config()
log = get_logger("drivers.imu_driver")
ESP_IP = CONFIG["imu_json"]["host"]
ESP_PORT = CONFIG["imu_json"]["port"]
TIMEOUT_SEC = CONFIG["imu_json"]["timeout"]


@dataclass
class IMUReading:
    seq: int
    ts: int  # μs since ESP boot
    ver: str
    errs: int
    yaw: float
    pitch: float
    roll: float
    accel: list[float]
    gyro: list[float]
    mag: list[float]
    pressure: float
    temp: float
    vbatt: float
    heap: int


class ESP32IMUClient:
    def __init__(self, host=ESP_IP, port=ESP_PORT, timeout=TIMEOUT_SEC):
        self.addr = (host, port)
        self.timeout = timeout
        self.sock = None
        self.fd = None

    def connect(self):
        self.sock = socket.create_connection(self.addr, self.timeout)
        self.fd = self.sock.makefile("r")

    def close(self):
        if self.fd:
            self.fd.close()
            self.fd = None
        if self.sock:
            self.sock.close()
            self.sock = None

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
                    seq=int(d["seq"]),
                    ts=int(d["ts"]),
                    ver=d["ver"],
                    errs=int(d["errs"]),
                    yaw=float(d["yaw"]),
                    pitch=float(d["pitch"]),
                    roll=float(d["roll"]),
                    accel=d["accel"],
                    gyro=d["gyro"],
                    mag=d["mag"],
                    pressure=float(d["pressure"]),
                    temp=float(d["temp"]),
                    vbatt=float(d["vbatt"]),
                    heap=int(d["heap"]),
                )
            except (ValueError, KeyError) as e:
                log.warning(f"JSON parse error: {e}")
                continue
