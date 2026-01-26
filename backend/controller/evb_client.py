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

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
