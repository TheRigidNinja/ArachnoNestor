#!/usr/bin/env python3
"""
Raw TCP client for EVB devices.
Handles reconnect, framing, CRC validation, and errors.
"""

from __future__ import annotations

import socket
from typing import Tuple

from protocol.evb_packets import PREAMBLE, MAX_PAYLOAD, ERROR, ERROR_CODES
from protocol.framing import build_packet, validate_response


class DeviceError(Exception):
    """Raised when the EVB returns an ERROR packet (0xE0)."""

    def __init__(self, orig_type: int, winch_id: int, code: int, message: str = ""):
        self.orig_type = orig_type
        self.winch_id = winch_id
        self.code = code
        self.message = message or f"code={code}"
        super().__init__(f"orig=0x{orig_type:02X} winch={winch_id} code={code} {self.message}")


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


def parse_error(payload: bytes):
    """Return (orig_type, winch_id, code, message)."""
    if len(payload) < 3:
        return (0, 0, -1, "malformed error payload")
    orig_type, winch_id, code = payload[0], payload[1], payload[2]
    return (orig_type, winch_id, code, ERROR_CODES.get(code, "unknown"))


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
        resp_type = header[1]
        resp_len = header[2]
        if resp_len > MAX_PAYLOAD:
            raise ValueError(f"response length {resp_len} exceeds max {MAX_PAYLOAD}")

        payload = read_exact(self.sock, resp_len)
        crc_byte = read_exact(self.sock, 1)[0]

        validate_response(header, payload, crc_byte)

        if resp_type == ERROR:
            orig, winch, code, msg = parse_error(payload)
            raise DeviceError(orig, winch, code, msg)

        return resp_type, payload


def send_command(host: str, port: int, timeout: float, type_byte: int, payload: bytes) -> Tuple[int, bytes]:
    """Send a command using a short-lived connection and return (type, payload)."""
    with EvbClient(host, port, timeout) as cli:
        return cli.send(type_byte, payload)
