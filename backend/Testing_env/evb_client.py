#!/usr/bin/env python3
"""
Small CLI client to talk to an ESP32-EVB over TCP.
Implements PING, GET_SNAPSHOT, GET_DELTA commands with CRC-8 verification.
"""

import argparse
import socket
import struct
import sys
from typing import Tuple


class DeviceError(Exception):
    """Raised when the EVB returns an ERROR packet (0xE0)."""

    def __init__(self, orig_type: int, winch_id: int, code: int, message: str = ""):
        self.orig_type = orig_type
        self.winch_id = winch_id
        self.code = code
        self.message = message or f"code={code}"
        super().__init__(f"orig=0x{orig_type:02X} winch={winch_id} code={code} {self.message}")

PREAMBLE = 0xAA
MAX_PAYLOAD = 64


def crc8(data: bytes, poly: int = 0x07, init: int = 0x00) -> int:
    """Compute CRC-8 with polynomial 0x07 over the given bytes."""
    crc = init
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) & 0xFF) ^ poly
            else:
                crc = (crc << 1) & 0xFF
    return crc


def read_exact(sock: socket.socket, size: int) -> bytes:
    """Read exactly size bytes or raise TimeoutError/ConnectionError."""
    buf = bytearray()
    while len(buf) < size:
        try:
            chunk = sock.recv(size - len(buf))
        except socket.timeout as exc:
            raise TimeoutError("socket read timed out") from exc
        if not chunk:
            raise ConnectionError("connection closed by peer")
        buf.extend(chunk)
    return bytes(buf)


def build_packet(type_byte: int, payload: bytes) -> bytes:
    if len(payload) > MAX_PAYLOAD:
        raise ValueError("payload too long")
    header = bytes([PREAMBLE, type_byte & 0xFF, len(payload)])
    crc = crc8(header + payload)
    return header + payload + bytes([crc])


class EvbClient:
    """
    Persistent TCP client to avoid connect/disconnect overhead.
    Use as a context manager.
    """

    def __init__(self, host: str, port: int, timeout: float = 1.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.sock: socket.socket | None = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    def connect(self):
        if self.sock:
            return
        self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self.sock.settimeout(self.timeout)

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            finally:
                self.sock = None

    def send(self, type_byte: int, payload: bytes) -> Tuple[int, bytes]:
        """Send one command over the persistent socket."""
        if not self.sock:
            self.connect()
        packet = build_packet(type_byte, payload)
        self.sock.sendall(packet)

        header = read_exact(self.sock, 3)
        if header[0] != PREAMBLE:
            raise ValueError(f"bad preamble 0x{header[0]:02X}")

        resp_type = header[1]
        resp_len = header[2]
        if resp_len > MAX_PAYLOAD:
            raise ValueError(f"response length {resp_len} exceeds max {MAX_PAYLOAD}")

        payload = read_exact(self.sock, resp_len)
        crc_byte = read_exact(self.sock, 1)[0]

        computed_crc = crc8(header + payload)
        if crc_byte != computed_crc:
            raise ValueError(f"CRC mismatch (got 0x{crc_byte:02X}, expected 0x{computed_crc:02X})")

        if resp_type == 0xE0:
            orig, winch, code, msg = parse_error(payload)
            raise DeviceError(orig, winch, code, msg)

        return resp_type, payload


def send_command(host: str, port: int, timeout: float, type_byte: int, payload: bytes) -> Tuple[int, bytes]:
    """Send a command using a short-lived connection and return (type, payload)."""
    with EvbClient(host, port, timeout) as cli:
        return cli.send(type_byte, payload)


def parse_error(payload: bytes):
    """Return (orig_type, winch_id, code, message)."""
    if len(payload) < 3:
        return (0, 0, -1, "malformed error payload")
    orig_type, winch_id, code = payload[0], payload[1], payload[2]
    code_map = {
        1: "bad length",
        2: "Compact timeout",
        3: "unknown command",
    }
    return (orig_type, winch_id, code, code_map.get(code, "unknown"))


def cmd_ping(args) -> int:
    try:
        resp_type, payload = send_command(args.host, args.port, args.timeout, 0x01, b"")
    except Exception as exc:
        print(f"ping failed: {exc}", file=sys.stderr)
        return 1

    if resp_type == 0xE0:
        print(parse_error(payload), file=sys.stderr)
        return 1
    if resp_type != 0x01 or payload:
        print(f"unexpected response type=0x{resp_type:02X} len={len(payload)}", file=sys.stderr)
        return 1
    print("ping ok")
    return 0


def cmd_snapshot(args) -> int:
    winch_id = args.winch_id & 0xFF
    try:
        resp_type, payload = send_command(args.host, args.port, args.timeout, 0x04, bytes([winch_id]))
    except Exception as exc:
        print(f"snapshot failed: {exc}", file=sys.stderr)
        return 1

    if resp_type == 0xE0:
        print(parse_error(payload), file=sys.stderr)
        return 1
    if resp_type != 0x04 or len(payload) != 7:
        print(f"unexpected response type=0x{resp_type:02X} len={len(payload)}", file=sys.stderr)
        return 1

    r_winch = payload[0]
    total_count, hall_raw = struct.unpack_from("<I H", payload, 1)
    print(f"snapshot winch={r_winch} total_count={total_count} hall_raw={hall_raw}")
    return 0


def cmd_delta(args) -> int:
    winch_id = args.winch_id & 0xFF
    try:
        resp_type, payload = send_command(args.host, args.port, args.timeout, 0x05, bytes([winch_id]))
    except Exception as exc:
        print(f"delta failed: {exc}", file=sys.stderr)
        return 1

    if resp_type == 0xE0:
        print(parse_error(payload), file=sys.stderr)
        return 1
    if resp_type != 0x05 or len(payload) != 5:
        print(f"unexpected response type=0x{resp_type:02X} len={len(payload)}", file=sys.stderr)
        return 1

    r_winch = payload[0]
    (delta_count,) = struct.unpack_from("<i", payload, 1)
    print(f"delta winch={r_winch} delta_count={delta_count}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ESP32-EVB TCP client")
    parser.add_argument("--host", default="192.168.2.123", help="ESP32-EVB IP address")
    parser.add_argument("--port", type=int, default=5000, help="ESP32-EVB TCP port")
    parser.add_argument("--timeout", type=float, default=1.0, help="socket timeout in seconds")

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
