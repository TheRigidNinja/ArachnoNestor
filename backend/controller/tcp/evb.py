#!/usr/bin/env python3
"""
EVB TCP communication helpers.

All sensor data access should go through this module.
"""

from __future__ import annotations

import struct

from tcp.client import EvbClient, DeviceError
from protocol.evb_packets import (
    ARM_ENCODER_TARGET,
    ARM_TENSION_TRIGGER,
    BUNDLE,
    DELTA,
    DISARM_ENCODER_TARGET,
    DISARM_TENSION_TRIGGER,
    DISTANCE,
    ERROR,
    EXPECTED_LENGTHS,
    GET_ENCODER_TARGET,
    GET_TENSION_TRIGGER,
    IMU,
    PING,
    SAVE_ENCODERS,
    SNAPSHOT,
    WAIT_ENCODER_TARGET,
    WAIT_TENSION_TRIGGER,
)


def _send(cli: EvbClient, msg_type: int, payload: bytes, timeout: float | None = None):
    if timeout is None:
        return cli.send(msg_type, payload)

    if not cli.sock:
        cli.connect()
    if not cli.sock:
        raise RuntimeError("EVB client failed to connect")

    old_timeout = cli.sock.gettimeout()
    cli.sock.settimeout(timeout)
    try:
        return cli.send(msg_type, payload)
    finally:
        if cli.sock:
            cli.sock.settimeout(old_timeout)

_LEGACY_SNAPSHOT_LEN = 7
_LEGACY_DELTA_LEN = 5
_LEGACY_DISTANCE_LEN = 9
_LEGACY_BUNDLE_LEN = 28
_LEGACY_IMU_LEN = 40


def _require_len(resp_type: int, payload: bytes, expected: int, legacy: int | None = None) -> None:
    if len(payload) == expected:
        return
    if legacy is not None and len(payload) == legacy:
        return
    if legacy is None:
        raise RuntimeError(f"bad response type=0x{resp_type:02X} len={len(payload)} expected={expected}")
    raise RuntimeError(
        f"bad response type=0x{resp_type:02X} len={len(payload)} expected={expected} or {legacy}"
    )


def get_snapshot(cli: EvbClient, winch_id: int):
    resp_type, payload = cli.send(SNAPSHOT, bytes([winch_id]))
    if resp_type == ERROR:
        raise RuntimeError(f"winch {winch_id}: device error (snapshot): {payload.hex()}")
    if resp_type != SNAPSHOT:
        raise RuntimeError(f"winch {winch_id}: bad snapshot response type=0x{resp_type:02X} len={len(payload)}")
    _require_len(resp_type, payload, EXPECTED_LENGTHS[SNAPSHOT], _LEGACY_SNAPSHOT_LEN)
    r_winch = payload[0]
    total_count = int.from_bytes(payload[1:5], "little", signed=True)
    hall_raw = int.from_bytes(payload[5:7], "little", signed=False)
    return r_winch, total_count, hall_raw


def get_delta(cli: EvbClient, winch_id: int):
    resp_type, payload = cli.send(DELTA, bytes([winch_id]))
    if resp_type == ERROR:
        raise RuntimeError(f"winch {winch_id}: device error (delta): {payload.hex()}")
    if resp_type != DELTA:
        raise RuntimeError(f"winch {winch_id}: bad delta response type=0x{resp_type:02X} len={len(payload)}")
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
    cache_age_ms = struct.unpack_from("<I", payload, 9)[0] if len(payload) >= 13 else None
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
    GET_BUNDLE (0x09) payload (32B):
    [winch_id][flags][total i32][delta i32][hall u16][dist u16][strength u16][temp_raw u16][age_ms u16][bus_mv u16][current_ma i16][power_mw u32]
    [cache_age_ms u32]
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
    cache_age_ms = struct.unpack_from("<I", payload, 28)[0] if len(payload) >= 32 else None
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
    [gyro 3x f32][accel 3x f32][temp f32][pitch f32][roll f32][yaw f32]
    [cache_age_ms u32]
    """
    resp_type, payload = cli.send(IMU, b"")
    if resp_type == ERROR:
        raise RuntimeError(f"IMU: device error: {payload.hex()}")
    if resp_type != IMU:
        raise RuntimeError(f"IMU: bad response type=0x{resp_type:02X} len={len(payload)}")
    _require_len(resp_type, payload, EXPECTED_LENGTHS[IMU], _LEGACY_IMU_LEN)
    vals = struct.unpack_from("<10f", payload, 0)
    cache_age_ms = struct.unpack_from("<I", payload, 40)[0] if len(payload) >= 44 else None
    return {
        "gyro": vals[0:3],
        "accel": vals[3:6],
        "temp_c": vals[6],
        "pitch": vals[7],
        "roll": vals[8],
        "yaw": vals[9],
        "cache_age_ms": cache_age_ms,
    }


def parse_encoder_target_status(payload: bytes) -> dict:
    if len(payload) != EXPECTED_LENGTHS[GET_ENCODER_TARGET]:
        raise RuntimeError(f"bad encoder target payload len={len(payload)}")
    winch, ok, active, hit = struct.unpack_from("<BBBB", payload, 0)
    start_count, target_delta, current_delta, current_total, hit_total = struct.unpack_from("<iiiii", payload, 4)
    return {
        "winch": winch,
        "ok": ok,
        "active": active,
        "hit": hit,
        "start_count": start_count,
        "target_delta": target_delta,
        "current_delta": current_delta,
        "current_total": current_total,
        "hit_total": hit_total,
    }


def parse_tension_trigger_status(payload: bytes) -> dict:
    if len(payload) != EXPECTED_LENGTHS[GET_TENSION_TRIGGER]:
        raise RuntimeError(f"bad tension trigger payload len={len(payload)}")
    winch, ok, active, hit, direction = struct.unpack_from("<BBBBb", payload, 0)
    threshold_raw, start_raw, current_raw, hit_raw = struct.unpack_from("<HHHH", payload, 6)
    current_total, hit_total = struct.unpack_from("<ii", payload, 14)
    return {
        "winch": winch,
        "ok": ok,
        "active": active,
        "hit": hit,
        "direction": direction,
        "threshold_raw": threshold_raw,
        "start_raw": start_raw,
        "current_raw": current_raw,
        "hit_raw": hit_raw,
        "current_total": current_total,
        "hit_total": hit_total,
    }


def arm_encoder_target(cli: EvbClient, winch_id: int, target_delta: int):
    payload = bytes([winch_id]) + struct.pack("<i", int(target_delta))
    resp_type, reply = cli.send(ARM_ENCODER_TARGET, payload)
    if resp_type != ARM_ENCODER_TARGET:
        raise RuntimeError(f"winch {winch_id}: bad arm target response type=0x{resp_type:02X}")
    return parse_encoder_target_status(reply)


def get_encoder_target(cli: EvbClient, winch_id: int):
    resp_type, payload = cli.send(GET_ENCODER_TARGET, bytes([winch_id]))
    if resp_type != GET_ENCODER_TARGET:
        raise RuntimeError(f"winch {winch_id}: bad target status response type=0x{resp_type:02X}")
    return parse_encoder_target_status(payload)


def wait_encoder_target(cli: EvbClient, winch_id: int, timeout_ms: int):
    payload = bytes([winch_id]) + struct.pack("<I", int(timeout_ms))
    timeout_s = getattr(cli, "timeout", 1.0)
    wait_timeout_s = max(timeout_s, (timeout_ms / 1000.0) + 1.0) if timeout_ms > 0 else None
    resp_type, reply = _send(cli, WAIT_ENCODER_TARGET, payload, wait_timeout_s)
    if resp_type != WAIT_ENCODER_TARGET:
        raise RuntimeError(f"winch {winch_id}: bad wait target response type=0x{resp_type:02X}")
    return parse_encoder_target_status(reply)


def disarm_encoder_target(cli: EvbClient, winch_id: int):
    resp_type, payload = cli.send(DISARM_ENCODER_TARGET, bytes([winch_id]))
    if resp_type != DISARM_ENCODER_TARGET:
        raise RuntimeError(f"winch {winch_id}: bad disarm target response type=0x{resp_type:02X}")
    return parse_encoder_target_status(payload)


def arm_tension_trigger(cli: EvbClient, winch_id: int, threshold_raw: int, direction: int):
    if not 0 <= int(threshold_raw) <= 0xFFFF:
        raise ValueError("threshold_raw must be 0..65535")
    if not -128 <= int(direction) <= 127:
        raise ValueError("direction must fit int8")
    payload = bytes([winch_id]) + struct.pack("<Hb", int(threshold_raw), int(direction))
    resp_type, reply = cli.send(ARM_TENSION_TRIGGER, payload)
    if resp_type != ARM_TENSION_TRIGGER:
        raise RuntimeError(f"winch {winch_id}: bad arm tension response type=0x{resp_type:02X}")
    return parse_tension_trigger_status(reply)


def get_tension_trigger(cli: EvbClient, winch_id: int):
    resp_type, payload = cli.send(GET_TENSION_TRIGGER, bytes([winch_id]))
    if resp_type != GET_TENSION_TRIGGER:
        raise RuntimeError(f"winch {winch_id}: bad tension status response type=0x{resp_type:02X}")
    return parse_tension_trigger_status(payload)


def wait_tension_trigger(cli: EvbClient, winch_id: int, timeout_ms: int):
    payload = bytes([winch_id]) + struct.pack("<I", int(timeout_ms))
    timeout_s = getattr(cli, "timeout", 1.0)
    wait_timeout_s = max(timeout_s, (timeout_ms / 1000.0) + 1.0) if timeout_ms > 0 else None
    resp_type, reply = _send(cli, WAIT_TENSION_TRIGGER, payload, wait_timeout_s)
    if resp_type != WAIT_TENSION_TRIGGER:
        raise RuntimeError(f"winch {winch_id}: bad wait tension response type=0x{resp_type:02X}")
    return parse_tension_trigger_status(reply)


def disarm_tension_trigger(cli: EvbClient, winch_id: int):
    resp_type, payload = cli.send(DISARM_TENSION_TRIGGER, bytes([winch_id]))
    if resp_type != DISARM_TENSION_TRIGGER:
        raise RuntimeError(f"winch {winch_id}: bad disarm tension response type=0x{resp_type:02X}")
    return parse_tension_trigger_status(payload)


def save_encoders(cli: EvbClient) -> bool:
    resp_type, payload = cli.send(SAVE_ENCODERS, b"")
    if resp_type != SAVE_ENCODERS or len(payload) != EXPECTED_LENGTHS[SAVE_ENCODERS]:
        raise RuntimeError(f"save encoders: bad response type=0x{resp_type:02X} len={len(payload)}")
    return payload[0] != 0


def ping(cli: EvbClient):
    resp_type, payload = cli.send(PING, b"")
    if resp_type == ERROR:
        raise RuntimeError(f"ping: device error: {payload.hex()}")
    if payload:
        raise RuntimeError(f"ping: unexpected payload len={len(payload)}")
    return True
