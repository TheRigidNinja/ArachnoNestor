"""EVB driver wrapper for typed sensor data."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from tcp.client import EvbClient, DeviceError
from tcp import evb as evb_api


@dataclass
class Bundle:
    winch: int
    flags: int
    total_count: int
    delta_count: int
    hall_raw: int
    dist_mm: int
    strength: int
    temp_raw: int
    age_ms: int
    bus_mv: int
    current_ma: int
    power_mw: int
    cache_age_ms: int | None


@dataclass
class Snapshot:
    winch: int
    total_count: int
    hall_raw: int


@dataclass
class Imu:
    gyro: tuple[float, float, float]
    accel: tuple[float, float, float]
    temp_c: float
    pitch: float
    roll: float
    yaw: float
    cache_age_ms: int | None
    seq: int | None


def get_bundle(cli: EvbClient, winch_id: int) -> Bundle:
    b = evb_api.get_bundle(cli, winch_id)
    return Bundle(
        winch=b["winch"],
        flags=b["flags"],
        total_count=b["total_count"],
        delta_count=b["delta_count"],
        hall_raw=b["hall_raw"],
        dist_mm=b["dist_mm"],
        strength=b["strength"],
        temp_raw=b["temp_raw"],
        age_ms=b["age_ms"],
        bus_mv=b["bus_mv"],
        current_ma=b["current_ma"],
        power_mw=b["power_mw"],
        cache_age_ms=b.get("cache_age_ms"),
    )


def get_snapshot(cli: EvbClient, winch_id: int) -> Snapshot:
    r_winch, total_count, hall_raw = evb_api.get_snapshot(cli, winch_id)
    return Snapshot(
        winch=r_winch,
        total_count=total_count,
        hall_raw=hall_raw,
    )


def get_imu(cli: EvbClient) -> Imu:
    i = evb_api.get_imu(cli)
    return Imu(
        gyro=tuple(i["gyro"]),
        accel=tuple(i["accel"]),
        temp_c=i["temp_c"],
        pitch=i["pitch"],
        roll=i["roll"],
        yaw=i["yaw"],
        cache_age_ms=i.get("cache_age_ms"),
        seq=i.get("seq"),
    )


def get_distance(cli: EvbClient) -> dict:
    return evb_api.get_distance(cli)


class EVBDriver:
    """Context-managed EVB driver using tcp/evb as gateway."""

    def __init__(self, host: str, port: int, timeout: float):
        self.host = host
        self.port = port
        self.timeout = timeout
        self._cli: EvbClient | None = None

    def __enter__(self):
        self._cli = EvbClient(self.host, self.port, self.timeout)
        self._cli.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._cli:
            self._cli.__exit__(exc_type, exc, tb)
            self._cli = None

    @property
    def client(self) -> EvbClient:
        if not self._cli:
            raise RuntimeError("EVBDriver not connected")
        return self._cli

    def bundle(self, winch_id: int) -> Bundle:
        return get_bundle(self.client, winch_id)

    def snapshot(self, winch_id: int) -> Snapshot:
        return get_snapshot(self.client, winch_id)

    def imu(self) -> Imu:
        return get_imu(self.client)

    def distance(self) -> dict:
        return get_distance(self.client)
