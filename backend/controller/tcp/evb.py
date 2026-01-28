#!/usr/bin/env python3
"""
EVB TCP communication helpers.

All sensor data access should go through this module.
"""

from __future__ import annotations

import struct

from tcp.client import EvbClient
from protocol.evb_packets import PING, SNAPSHOT, DELTA, DISTANCE, BUNDLE, IMU, ERROR, EXPECTED_LENGTHS


def get_snapshot(cli: EvbClient, winch_id: int):
    resp_type, payload = cli.send(SNAPSHOT, bytes([winch_id]))
    if resp_type == ERROR:
        raise RuntimeError(f"winch {winch_id}: device error (snapshot): {payload.hex()}")
    if resp_type != SNAPSHOT or len(payload) != EXPECTED_LENGTHS[SNAPSHOT]:
        raise RuntimeError(f"winch {winch_id}: bad snapshot response type=0x{resp_type:02X} len={len(payload)}")
    r_winch = payload[0]
    total_count = int.from_bytes(payload[1:5], "little", signed=False)
    hall_raw = int.from_bytes(payload[5:7], "little", signed=False)
    cache_age_ms = int.from_bytes(payload[7:11], "little", signed=False)
    return r_winch, total_count, hall_raw, cache_age_ms


def get_delta(cli: EvbClient, winch_id: int):
    resp_type, payload = cli.send(DELTA, bytes([winch_id]))
    if resp_type == ERROR:
        raise RuntimeError(f"winch {winch_id}: device error (delta): {payload.hex()}")
    if resp_type != DELTA or len(payload) != EXPECTED_LENGTHS[DELTA]:
        raise RuntimeError(f"winch {winch_id}: bad delta response type=0x{resp_type:02X} len={len(payload)}")
    r_winch = payload[0]
    delta_count = int.from_bytes(payload[1:5], "little", signed=True)
    cache_age_ms = int.from_bytes(payload[5:9], "little", signed=False)
    return r_winch, delta_count, cache_age_ms


def get_distance(cli: EvbClient):
    """
    GET_DISTANCE (0x07) payload (13 bytes):
    [ok u8][dist u16][strength u16][temp_raw u16][age_ms u16][cache_age_ms u32]
    """
    resp_type, payload = cli.send(DISTANCE, b"")
    if resp_type == ERROR:
        raise RuntimeError(f"distance: device error: {payload.hex()}")
    if resp_type != DISTANCE or len(payload) != EXPECTED_LENGTHS[DISTANCE]:
        raise RuntimeError(f"distance: bad response type=0x{resp_type:02X} len={len(payload)}")
    ok = payload[0]
    dist, strength, temp_raw, age_ms = struct.unpack_from("<4H", payload, 1)
    cache_age_ms = struct.unpack_from("<I", payload, 9)[0]
    return {
        "ok": ok,
        "dist_mm": dist,
        "strength": strength,
        "temp_raw": temp_raw,
        "age_ms": age_ms,
        "cache_age_ms": cache_age_ms,
    }


def get_bundle(cli: EvbClient, winch_id: int):
    """
    GET_BUNDLE (0x09) payload (28B):
    [winch_id][flags][total i32][delta i32][hall u16][dist u16][strength u16][temp_raw u16][age_ms u16][bus_mv u16][current_ma i16][power_mw u32]
    """
    resp_type, payload = cli.send(BUNDLE, bytes([winch_id]))
    if resp_type == ERROR:
        raise RuntimeError(f"winch {winch_id}: device error (bundle): {payload.hex()}")
    if resp_type != BUNDLE or len(payload) != EXPECTED_LENGTHS[BUNDLE]:
        raise RuntimeError(f"winch {winch_id}: bad bundle response type=0x{resp_type:02X} len={len(payload)}")
    (r_winch, flags) = struct.unpack_from("<BB", payload, 0)
    total_count = struct.unpack_from("<i", payload, 2)[0]
    delta_count = struct.unpack_from("<i", payload, 6)[0]
    hall_raw = struct.unpack_from("<H", payload, 10)[0]
    dist, strength, temp_raw, age_ms = struct.unpack_from("<4H", payload, 12)
    bus_mv = struct.unpack_from("<H", payload, 20)[0]
    current_ma = struct.unpack_from("<h", payload, 22)[0]
    power_mw = struct.unpack_from("<I", payload, 24)[0]
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
    if resp_type != IMU or len(payload) != EXPECTED_LENGTHS[IMU]:
        raise RuntimeError(f"IMU: bad response type=0x{resp_type:02X} len={len(payload)}")
    vals = struct.unpack("<10f", payload[:40])
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


def ping(cli: EvbClient):
    resp_type, payload = cli.send(PING, b"")
    if resp_type == ERROR:
        raise RuntimeError(f"ping: device error: {payload.hex()}")
    if payload:
        raise RuntimeError(f"ping: unexpected payload len={len(payload)}")
    return True
