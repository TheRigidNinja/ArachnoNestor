"""EVB packet definitions and payload layouts."""

PREAMBLE = 0xAA
MAX_PAYLOAD = 64

# Message types
PING = 0x01
SNAPSHOT = 0x04
DELTA = 0x05
DISTANCE = 0x07
POWER = 0x08
BUNDLE = 0x09
IMU = 0x0A
ERROR = 0xE0

# Expected payload lengths
EXPECTED_LENGTHS = {
    SNAPSHOT: 7,
    DELTA: 5,
    DISTANCE: 9,
    BUNDLE: 28,
    IMU: 40,
}

ERROR_CODES = {
    1: "bad length",
    2: "compact timeout",
    3: "unknown command",
}
