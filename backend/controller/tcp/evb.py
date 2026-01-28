#!/usr/bin/env python3
"""
EVB TCP communication helpers.

All sensor data access should go through this module.
"""

from __future__ import annotations

import struct

from tcp.client import EvbClient
from protocol.evb_packets import (
    PING,
    SNAPSHOT,
    DELTA,
    DISTANCE,
    POWER,
    BUNDLE,
    IMU,
    STREAM_STRIDE,
    STREAM_BUNDLE,
    STREAM_DISTANCE,
    STREAM_IMU,
    ERROR,
    EXPECTED_LENGTHS,
)

_LEGACY_SNAPSHOT_LEN = 7
_LEGACY_DELTA_LEN = 5
_LEGACY_DISTANCE_LEN = 9
_LEGACY_BUNDLE_LEN = 28
_LEGACY_IMU_LEN = 40


def _require_len(resp_type: int, payload: bytes, expected: int, alt_expected: int | None = None) -> None:
    if len(payload) == expected:
        return
    if alt_expected is not None and len(payload) == alt_expected:
        return
    if alt_expected is None:
        raise RuntimeError(
            f"bad response type=0x{resp_type:02X} len={len(payload)} expected={expected}"
        )
    raise RuntimeError(
        f"bad response type=0x{resp_type:02X} len={len(payload)} "
        f"expected={expected} or {alt_expected}"
    )


def get_snapshot(cli: EvbClient, winch_id: int):
    resp_type, payload = cli.send(SNAPSHOT, bytes([winch_id]))
    if resp_type == ERROR:
        raise RuntimeError(f"winch {winch_id}: device error (snapshot): {payload.hex()}")
    if resp_type != SNAPSHOT:
        raise RuntimeError(
            f"winch {winch_id}: bad snapshot response type=0x{resp_type:02X} len={len(payload)}"
        )
    _require_len(resp_type, payload, EXPECTED_LENGTHS[SNAPSHOT], _LEGACY_SNAPSHOT_LEN)
    r_winch = payload[0]
    total_count = int.from_bytes(payload[1:5], "little", signed=False)
    hall_raw = int.from_bytes(payload[5:7], "little", signed=False)
    return r_winch, total_count, hall_raw


def get_delta(cli: EvbClient, winch_id: int):
    resp_type, payload = cli.send(DELTA, bytes([winch_id]))
    if resp_type == ERROR:
        raise RuntimeError(f"winch {winch_id}: device error (delta): {payload.hex()}")
    if resp_type != DELTA:
        raise RuntimeError(
            f"winch {winch_id}: bad delta response type=0x{resp_type:02X} len={len(payload)}"
        )
    _require_len(resp_type, payload, EXPECTED_LENGTHS[DELTA], _LEGACY_DELTA_LEN)
    r_winch = payload[0]
    delta_count = int.from_bytes(payload[1:5], "little", signed=True)
    return r_winch, delta_count


def get_distance(cli: EvbClient):
    """
    GET_DISTANCE (0x07) payload (13 bytes):
    [ok u8][dist u16][strength u16][temp_raw u16][age_ms u16][cache_age_ms u32]
    """
    resp_type, payload = cli.send(DISTANCE, b"")
    if resp_type == ERROR:
        raise RuntimeError(f"distance: device error: {payload.hex()}")
    if resp_type != DISTANCE:
        raise RuntimeError(f"distance: bad response type=0x{resp_type:02X} len={len(payload)}")
    _require_len(resp_type, payload, EXPECTED_LENGTHS[DISTANCE], _LEGACY_DISTANCE_LEN)
    ok = payload[0]
    dist, strength, temp_raw, age_ms = struct.unpack_from("<4H", payload, 1)
    cache_age_ms = None
    if len(payload) >= 13:
        cache_age_ms = struct.unpack_from("<I", payload, 9)[0]
    return {
        "ok": ok,
        "dist_mm": dist,
        "strength": strength,
        "temp_raw": temp_raw,
        "age_ms": age_ms,
        "cache_age_ms": cache_age_ms,
    }


def get_power(cli: EvbClient, winch_id: int):
    """
    GET_POWER (0x08) payload (13 bytes):
    [winch_id u8][bus_mv u16][current_ma i16][power_mw u32][cache_age_ms u32]
    """
    resp_type, payload = cli.send(POWER, bytes([winch_id]))
    if resp_type == ERROR:
        raise RuntimeError(f"winch {winch_id}: device error (power): {payload.hex()}")
    if resp_type != POWER:
        raise RuntimeError(f"winch {winch_id}: bad power response type=0x{resp_type:02X} len={len(payload)}")
    _require_len(resp_type, payload, EXPECTED_LENGTHS[POWER])
    r_winch = payload[0]
    bus_mv = struct.unpack_from("<H", payload, 1)[0]
    current_ma = struct.unpack_from("<h", payload, 3)[0]
    power_mw = struct.unpack_from("<I", payload, 5)[0]
    cache_age_ms = struct.unpack_from("<I", payload, 9)[0]
    return {
        "winch": r_winch,
        "bus_mv": bus_mv,
        "current_ma": current_ma,
        "power_mw": power_mw,
        "cache_age_ms": cache_age_ms,
    }


def get_bundle(cli: EvbClient, winch_id: int):
    """
    GET_BUNDLE (0x09) payload (32B):
    [winch_id][flags][total i32][delta i32][hall u16][dist u16][strength u16][temp_raw u16]
    [age_ms u16][bus_mv u16][current_ma i16][power_mw u32][cache_age_ms u32]
    """
    resp_type, payload = cli.send(BUNDLE, bytes([winch_id]))
    if resp_type == ERROR:
        raise RuntimeError(f"winch {winch_id}: device error (bundle): {payload.hex()}")
    if resp_type != BUNDLE:
        raise RuntimeError(f"winch {winch_id}: bad bundle response type=0x{resp_type:02X} len={len(payload)}")
    _require_len(resp_type, payload, EXPECTED_LENGTHS[BUNDLE], _LEGACY_BUNDLE_LEN)
    (r_winch, flags) = struct.unpack_from("<BB", payload, 0)
    total_count = struct.unpack_from("<i", payload, 2)[0]
    delta_count = struct.unpack_from("<i", payload, 6)[0]
    hall_raw = struct.unpack_from("<H", payload, 10)[0]
    dist, strength, temp_raw, age_ms = struct.unpack_from("<4H", payload, 12)
    bus_mv = struct.unpack_from("<H", payload, 20)[0]
    current_ma = struct.unpack_from("<h", payload, 22)[0]
    power_mw = struct.unpack_from("<I", payload, 24)[0]
    cache_age_ms = None
    if len(payload) >= 32:
        cache_age_ms = struct.unpack_from("<I", payload, 28)[0]
    return {
        "winch": r_winch,
        "flags": flags,
        "total_count": total_count,
        "delta_count": delta_count,
        "hall_raw": hall_raw,
        "dist_mm": dist,
        "strength": strength,
        "temp_raw": temp_raw,
        "age_ms": age_ms,
        "bus_mv": bus_mv,
        "current_ma": current_ma,
        "power_mw": power_mw,
        "cache_age_ms": cache_age_ms,
    }


def get_imu(cli: EvbClient):
    """
    GET_IMU (0x0A) payload (44B):
    [gyro 3x f32][accel 3x f32][temp f32][pitch f32][roll f32][yaw f32][cache_age_ms u32]
    """
    resp_type, payload = cli.send(IMU, b"")
    if resp_type == ERROR:
        raise RuntimeError(f"IMU: device error: {payload.hex()}")
    if resp_type != IMU:
        raise RuntimeError(f"IMU: bad response type=0x{resp_type:02X} len={len(payload)}")
    _require_len(resp_type, payload, EXPECTED_LENGTHS[IMU], _LEGACY_IMU_LEN)
    vals = struct.unpack_from("<10f", payload, 0)
    cache_age_ms = None
    if len(payload) >= 44:
        cache_age_ms = struct.unpack_from("<I", payload, 40)[0]
    return {
        "gyro": vals[0:3],
        "accel": vals[3:6],
        "temp_c": vals[6],
        "pitch": vals[7],
        "roll": vals[8],
        "yaw": vals[9],
        "cache_age_ms": cache_age_ms,
    }


def set_stream_stride(cli: EvbClient, stride_u16: int) -> int:
    payload = struct.pack("<H", stride_u16)
    resp_type, resp_payload = cli.send(STREAM_STRIDE, payload)
    if resp_type == ERROR:
        raise RuntimeError(f"stream stride: device error: {resp_payload.hex()}")
    if resp_type != STREAM_STRIDE:
        raise RuntimeError(f"stream stride: bad response type=0x{resp_type:02X} len={len(resp_payload)}")
    _require_len(resp_type, resp_payload, EXPECTED_LENGTHS[STREAM_STRIDE])
    return struct.unpack_from("<H", resp_payload, 0)[0]


def stream_bundle(cli: EvbClient, winch_id: int):
    """
    STREAM_BUNDLE (0x0C) payload (36B):
    [winch_id][flags][total i32][delta i32][hall u16][dist u16][strength u16][temp_raw u16]
    [age_ms u16][bus_mv u16][current_ma i16][power_mw u32][cache_age_ms u32][seq u32]
    """
    resp_type, payload = cli.send(STREAM_BUNDLE, bytes([winch_id]))
    if resp_type == ERROR:
        raise RuntimeError(f"winch {winch_id}: device error (stream bundle): {payload.hex()}")
    if resp_type != STREAM_BUNDLE:
        raise RuntimeError(f"winch {winch_id}: bad stream bundle response type=0x{resp_type:02X} len={len(payload)}")
    _require_len(resp_type, payload, EXPECTED_LENGTHS[STREAM_BUNDLE])
    (r_winch, flags) = struct.unpack_from("<BB", payload, 0)
    total_count = struct.unpack_from("<i", payload, 2)[0]
    delta_count = struct.unpack_from("<i", payload, 6)[0]
    hall_raw = struct.unpack_from("<H", payload, 10)[0]
    dist, strength, temp_raw, age_ms = struct.unpack_from("<4H", payload, 12)
    bus_mv = struct.unpack_from("<H", payload, 20)[0]
    current_ma = struct.unpack_from("<h", payload, 22)[0]
    power_mw = struct.unpack_from("<I", payload, 24)[0]
    cache_age_ms = struct.unpack_from("<I", payload, 28)[0]
    seq = struct.unpack_from("<I", payload, 32)[0]
    return {
        "winch": r_winch,
        "flags": flags,
        "total_count": total_count,
        "delta_count": delta_count,
        "hall_raw": hall_raw,
        "dist_mm": dist,
        "strength": strength,
        "temp_raw": temp_raw,
        "age_ms": age_ms,
        "bus_mv": bus_mv,
        "current_ma": current_ma,
        "power_mw": power_mw,
        "cache_age_ms": cache_age_ms,
        "seq": seq,
    }


def stream_distance(cli: EvbClient):
    """
    STREAM_DISTANCE (0x0D) payload (17B):
    [ok u8][dist u16][strength u16][temp_raw u16][age_ms u16][cache_age_ms u32][seq u32]
    """
    resp_type, payload = cli.send(STREAM_DISTANCE, b"")
    if resp_type == ERROR:
        raise RuntimeError(f"stream distance: device error: {payload.hex()}")
    if resp_type != STREAM_DISTANCE:
        raise RuntimeError(f"stream distance: bad response type=0x{resp_type:02X} len={len(payload)}")
    _require_len(resp_type, payload, EXPECTED_LENGTHS[STREAM_DISTANCE])
    ok = payload[0]
    dist, strength, temp_raw, age_ms = struct.unpack_from("<4H", payload, 1)
    cache_age_ms = struct.unpack_from("<I", payload, 9)[0]
    seq = struct.unpack_from("<I", payload, 13)[0]
    return {
        "ok": ok,
        "dist_mm": dist,
        "strength": strength,
        "temp_raw": temp_raw,
        "age_ms": age_ms,
        "cache_age_ms": cache_age_ms,
        "seq": seq,
    }


def stream_imu(cli: EvbClient):
    """
    STREAM_IMU (0x0E) payload (48B):
    [gyro 3x f32][accel 3x f32][temp f32][pitch f32][roll f32][yaw f32][cache_age_ms u32][seq u32]
    """
    resp_type, payload = cli.send(STREAM_IMU, b"")
    if resp_type == ERROR:
        raise RuntimeError(f"stream IMU: device error: {payload.hex()}")
    if resp_type != STREAM_IMU:
        raise RuntimeError(f"stream IMU: bad response type=0x{resp_type:02X} len={len(payload)}")
    _require_len(resp_type, payload, EXPECTED_LENGTHS[STREAM_IMU])
    vals = struct.unpack_from("<10f", payload, 0)
    cache_age_ms = struct.unpack_from("<I", payload, 40)[0]
    seq = struct.unpack_from("<I", payload, 44)[0]
    return {
        "gyro": vals[0:3],
        "accel": vals[3:6],
        "temp_c": vals[6],
        "pitch": vals[7],
        "roll": vals[8],
        "yaw": vals[9],
        "cache_age_ms": cache_age_ms,
        "seq": seq,
    }
def ping(cli: EvbClient):
    resp_type, payload = cli.send(PING, b"")
    if resp_type == ERROR:
        raise RuntimeError(f"ping: device error: {payload.hex()}")
    if payload:
        raise RuntimeError(f"ping: unexpected payload len={len(payload)}")
    return True
