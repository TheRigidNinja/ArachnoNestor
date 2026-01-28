"""Framing helpers for EVB packets."""

from protocol.crc8 import crc8
from protocol.evb_packets import PREAMBLE, MAX_PAYLOAD


def build_packet(type_byte: int, payload: bytes) -> bytes:
    if len(payload) > MAX_PAYLOAD:
        raise ValueError("payload too long")
    header = bytes([PREAMBLE, type_byte & 0xFF, len(payload)])
    crc = crc8(header + payload)
    return header + payload + bytes([crc])


def validate_response(header: bytes, payload: bytes, crc_byte: int) -> None:
    if len(header) != 3:
        raise ValueError("invalid header length")
    if header[0] != PREAMBLE:
        raise ValueError(f"bad preamble 0x{header[0]:02X}")
    computed_crc = crc8(header + payload)
    if crc_byte != computed_crc:
        raise ValueError(f"CRC mismatch (got 0x{crc_byte:02X}, expected 0x{computed_crc:02X})")
