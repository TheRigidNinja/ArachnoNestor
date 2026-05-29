"""EVB driver wrapper for typed sensor data."""

from __future__ import annotations

from dataclasses import dataclass

from tcp.client import EvbClient, DeviceError
from tcp import evb as evb_api


@dataclass
class Delta:
    winch: int
    delta_count: int


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
    cache_age_ms: int | None = None


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
    cache_age_ms: int | None = None


@dataclass
class EncoderTarget:
    winch: int
    ok: int
    active: int
    hit: int
    start_count: int
    target_delta: int
    current_delta: int
    current_total: int
    hit_total: int


@dataclass
class TensionTrigger:
    winch: int
    ok: int
    active: int
    hit: int
    direction: int
    threshold_raw: int
    start_raw: int
    current_raw: int
    hit_raw: int
    current_total: int
    hit_total: int


def _target_from_dict(status: dict) -> EncoderTarget:
    return EncoderTarget(
        winch=status["winch"],
        ok=status["ok"],
        active=status["active"],
        hit=status["hit"],
        start_count=status["start_count"],
        target_delta=status["target_delta"],
        current_delta=status["current_delta"],
        current_total=status["current_total"],
        hit_total=status["hit_total"],
    )


def _tension_from_dict(status: dict) -> TensionTrigger:
    return TensionTrigger(
        winch=status["winch"],
        ok=status["ok"],
        active=status["active"],
        hit=status["hit"],
        direction=status["direction"],
        threshold_raw=status["threshold_raw"],
        start_raw=status["start_raw"],
        current_raw=status["current_raw"],
        hit_raw=status["hit_raw"],
        current_total=status["current_total"],
        hit_total=status["hit_total"],
    )


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


def get_delta(cli: EvbClient, winch_id: int) -> Delta:
    r_winch, delta_count = evb_api.get_delta(cli, winch_id)
    return Delta(
        winch=r_winch,
        delta_count=delta_count,
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
    )


def get_distance(cli: EvbClient) -> dict:
    return evb_api.get_distance(cli)


def arm_encoder_target(cli: EvbClient, winch_id: int, target_delta: int) -> EncoderTarget:
    return _target_from_dict(evb_api.arm_encoder_target(cli, winch_id, target_delta))


def get_encoder_target(cli: EvbClient, winch_id: int) -> EncoderTarget:
    return _target_from_dict(evb_api.get_encoder_target(cli, winch_id))


def wait_encoder_target(cli: EvbClient, winch_id: int, timeout_ms: int) -> EncoderTarget:
    return _target_from_dict(evb_api.wait_encoder_target(cli, winch_id, timeout_ms))


def disarm_encoder_target(cli: EvbClient, winch_id: int) -> EncoderTarget:
    return _target_from_dict(evb_api.disarm_encoder_target(cli, winch_id))


def arm_tension_trigger(cli: EvbClient, winch_id: int, threshold_raw: int, direction: int) -> TensionTrigger:
    return _tension_from_dict(evb_api.arm_tension_trigger(cli, winch_id, threshold_raw, direction))


def get_tension_trigger(cli: EvbClient, winch_id: int) -> TensionTrigger:
    return _tension_from_dict(evb_api.get_tension_trigger(cli, winch_id))


def wait_tension_trigger(cli: EvbClient, winch_id: int, timeout_ms: int) -> TensionTrigger:
    return _tension_from_dict(evb_api.wait_tension_trigger(cli, winch_id, timeout_ms))


def disarm_tension_trigger(cli: EvbClient, winch_id: int) -> TensionTrigger:
    return _tension_from_dict(evb_api.disarm_tension_trigger(cli, winch_id))


def save_encoders(cli: EvbClient) -> bool:
    return evb_api.save_encoders(cli)


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

    def delta(self, winch_id: int) -> Delta:
        return get_delta(self.client, winch_id)

    def imu(self) -> Imu:
        return get_imu(self.client)

    def distance(self) -> dict:
        return get_distance(self.client)

    def ping(self) -> bool:
        return evb_api.ping(self.client)

    def arm_encoder_target(self, winch_id: int, target_delta: int) -> EncoderTarget:
        return arm_encoder_target(self.client, winch_id, target_delta)

    def encoder_target(self, winch_id: int) -> EncoderTarget:
        return get_encoder_target(self.client, winch_id)

    def wait_encoder_target(self, winch_id: int, timeout_ms: int) -> EncoderTarget:
        return wait_encoder_target(self.client, winch_id, timeout_ms)

    def disarm_encoder_target(self, winch_id: int) -> EncoderTarget:
        return disarm_encoder_target(self.client, winch_id)

    def arm_tension_trigger(self, winch_id: int, threshold_raw: int, direction: int) -> TensionTrigger:
        return arm_tension_trigger(self.client, winch_id, threshold_raw, direction)

    def tension_trigger(self, winch_id: int) -> TensionTrigger:
        return get_tension_trigger(self.client, winch_id)

    def wait_tension_trigger(self, winch_id: int, timeout_ms: int) -> TensionTrigger:
        return wait_tension_trigger(self.client, winch_id, timeout_ms)

    def disarm_tension_trigger(self, winch_id: int) -> TensionTrigger:
        return disarm_tension_trigger(self.client, winch_id)

    def save_encoders(self) -> bool:
        return save_encoders(self.client)
