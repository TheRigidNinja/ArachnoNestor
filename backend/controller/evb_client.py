#!/usr/bin/env python3
"""Compatibility wrapper for EVB TCP client."""

import argparse
import sys

from config.settings import load_config
from drivers.evb_driver import EVBDriver
from tcp import evb as evb_api


def cmd_ping(args) -> int:
    try:
        with EVBDriver(args.host, args.port, args.timeout) as evb:
            evb_api.ping(evb.client)
    except Exception as exc:
        print(f"ping failed: {exc}", file=sys.stderr)
        return 1
    print("ping ok")
    return 0


def cmd_snapshot(args) -> int:
    winch_id = args.winch_id & 0xFF
    try:
        with EVBDriver(args.host, args.port, args.timeout) as evb:
            r_winch, total_count, hall_raw = evb_api.get_snapshot(evb.client, winch_id)
    except Exception as exc:
        print(f"snapshot failed: {exc}", file=sys.stderr)
        return 1
    print(f"snapshot winch={r_winch} total_count={total_count} hall_raw={hall_raw}")
    return 0


def cmd_delta(args) -> int:
    winch_id = args.winch_id & 0xFF
    try:
        with EVBDriver(args.host, args.port, args.timeout) as evb:
            r_winch, delta_count = evb_api.get_delta(evb.client, winch_id)
    except Exception as exc:
        print(f"delta failed: {exc}", file=sys.stderr)
        return 1
    print(f"delta winch={r_winch} delta_count={delta_count}")
    return 0


def print_target_status(label: str, status) -> None:
    print(
        f"{label} winch={status.winch} ok={status.ok} active={status.active} "
        f"hit={status.hit} start_count={status.start_count} "
        f"target_delta={status.target_delta} current_delta={status.current_delta} "
        f"current_total={status.current_total} hit_total={status.hit_total}"
    )


def print_tension_status(label: str, status) -> None:
    print(
        f"{label} winch={status.winch} ok={status.ok} active={status.active} "
        f"hit={status.hit} direction={status.direction} "
        f"threshold_raw={status.threshold_raw} start_raw={status.start_raw} "
        f"current_raw={status.current_raw} hit_raw={status.hit_raw} "
        f"current_total={status.current_total} hit_total={status.hit_total}"
    )


def cmd_arm_target(args) -> int:
    try:
        with EVBDriver(args.host, args.port, args.timeout) as evb:
            status = evb.arm_encoder_target(args.winch_id & 0xFF, args.target_delta)
    except Exception as exc:
        print(f"arm-target failed: {exc}", file=sys.stderr)
        return 1
    print_target_status("target armed", status)
    return 0


def cmd_target_status(args) -> int:
    try:
        with EVBDriver(args.host, args.port, args.timeout) as evb:
            status = evb.encoder_target(args.winch_id & 0xFF)
    except Exception as exc:
        print(f"target-status failed: {exc}", file=sys.stderr)
        return 1
    print_target_status("target status", status)
    return 0


def cmd_wait_target(args) -> int:
    try:
        with EVBDriver(args.host, args.port, args.timeout) as evb:
            status = evb.wait_encoder_target(args.winch_id & 0xFF, args.timeout_ms)
    except Exception as exc:
        print(f"wait-target failed: {exc}", file=sys.stderr)
        return 1
    print_target_status("target wait", status)
    return 0 if status.hit else 2


def cmd_disarm_target(args) -> int:
    try:
        with EVBDriver(args.host, args.port, args.timeout) as evb:
            status = evb.disarm_encoder_target(args.winch_id & 0xFF)
    except Exception as exc:
        print(f"disarm-target failed: {exc}", file=sys.stderr)
        return 1
    print_target_status("target disarmed", status)
    return 0


def cmd_arm_tension(args) -> int:
    try:
        with EVBDriver(args.host, args.port, args.timeout) as evb:
            status = evb.arm_tension_trigger(args.winch_id & 0xFF, args.threshold_raw, args.direction)
    except Exception as exc:
        print(f"arm-tension failed: {exc}", file=sys.stderr)
        return 1
    print_tension_status("tension armed", status)
    return 0


def cmd_tension_status(args) -> int:
    try:
        with EVBDriver(args.host, args.port, args.timeout) as evb:
            status = evb.tension_trigger(args.winch_id & 0xFF)
    except Exception as exc:
        print(f"tension-status failed: {exc}", file=sys.stderr)
        return 1
    print_tension_status("tension status", status)
    return 0


def cmd_wait_tension(args) -> int:
    try:
        with EVBDriver(args.host, args.port, args.timeout) as evb:
            status = evb.wait_tension_trigger(args.winch_id & 0xFF, args.timeout_ms)
    except Exception as exc:
        print(f"wait-tension failed: {exc}", file=sys.stderr)
        return 1
    print_tension_status("tension wait", status)
    return 0 if status.hit else 2


def cmd_disarm_tension(args) -> int:
    try:
        with EVBDriver(args.host, args.port, args.timeout) as evb:
            status = evb.disarm_tension_trigger(args.winch_id & 0xFF)
    except Exception as exc:
        print(f"disarm-tension failed: {exc}", file=sys.stderr)
        return 1
    print_tension_status("tension disarmed", status)
    return 0


def cmd_save_encoders(args) -> int:
    try:
        with EVBDriver(args.host, args.port, args.timeout) as evb:
            ok = evb.save_encoders()
    except Exception as exc:
        print(f"save-encoders failed: {exc}", file=sys.stderr)
        return 1
    print(f"save encoders ok={int(ok)}")
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    cfg = load_config()
    parser = argparse.ArgumentParser(description="ESP32-EVB TCP client")
    parser.add_argument("--host", default=cfg["evb"]["host"], help="ESP32-EVB IP address")
    parser.add_argument("--port", type=int, default=cfg["evb"]["port"], help="ESP32-EVB TCP port")
    parser.add_argument("--timeout", type=float, default=cfg["evb"]["timeout"], help="socket timeout in seconds")

    sub = parser.add_subparsers(dest="command", required=True)

    ping_p = sub.add_parser("ping", help="send ping")
    ping_p.set_defaults(func=cmd_ping)

    snap_p = sub.add_parser("snapshot", help="get snapshot for winch")
    snap_p.add_argument("winch_id", type=int, help="winch id (0-255)")
    snap_p.set_defaults(func=cmd_snapshot)

    delta_p = sub.add_parser("delta", help="get delta for winch")
    delta_p.add_argument("winch_id", type=int, help="winch id (0-255)")
    delta_p.set_defaults(func=cmd_delta)

    arm_target_p = sub.add_parser("arm-target", help="arm a Compact encoder delta target")
    arm_target_p.add_argument("winch_id", type=int, help="winch id (1-4)")
    arm_target_p.add_argument("target_delta", type=int, help="encoder delta from the arm point")
    arm_target_p.set_defaults(func=cmd_arm_target)

    target_status_p = sub.add_parser("target-status", help="read encoder target status")
    target_status_p.add_argument("winch_id", type=int, help="winch id (1-4)")
    target_status_p.set_defaults(func=cmd_target_status)

    wait_target_p = sub.add_parser("wait-target", help="wait until Compact reports target hit")
    wait_target_p.add_argument("winch_id", type=int, help="winch id (1-4)")
    wait_target_p.add_argument("timeout_ms", type=int, help="timeout in ms; 0 means no EVB-side timeout")
    wait_target_p.set_defaults(func=cmd_wait_target)

    disarm_target_p = sub.add_parser("disarm-target", help="disarm encoder target")
    disarm_target_p.add_argument("winch_id", type=int, help="winch id (1-4)")
    disarm_target_p.set_defaults(func=cmd_disarm_target)

    arm_tension_p = sub.add_parser("arm-tension", help="arm a Hall tension trigger")
    arm_tension_p.add_argument("winch_id", type=int, help="winch id (1-4)")
    arm_tension_p.add_argument("threshold_raw", type=int, help="raw Hall ADC threshold")
    arm_tension_p.add_argument("direction", type=int, help="direction hint for motor motion, usually -1 or 1")
    arm_tension_p.set_defaults(func=cmd_arm_tension)

    tension_status_p = sub.add_parser("tension-status", help="read Hall tension trigger status")
    tension_status_p.add_argument("winch_id", type=int, help="winch id (1-4)")
    tension_status_p.set_defaults(func=cmd_tension_status)

    wait_tension_p = sub.add_parser("wait-tension", help="wait until Hall tension trigger fires")
    wait_tension_p.add_argument("winch_id", type=int, help="winch id (1-4)")
    wait_tension_p.add_argument("timeout_ms", type=int, help="timeout in ms; 0 means no EVB-side timeout")
    wait_tension_p.set_defaults(func=cmd_wait_tension)

    disarm_tension_p = sub.add_parser("disarm-tension", help="disarm Hall tension trigger")
    disarm_tension_p.add_argument("winch_id", type=int, help="winch id (1-4)")
    disarm_tension_p.set_defaults(func=cmd_disarm_tension)

    save_p = sub.add_parser("save-encoders", help="force Compact to persist encoder totals")
    save_p.set_defaults(func=cmd_save_encoders)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
