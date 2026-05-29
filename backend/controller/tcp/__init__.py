"""TCP communication modules for EVB devices."""

from .client import EvbClient, DeviceError, send_command
from .evb import (
    arm_encoder_target,
    arm_tension_trigger,
    disarm_encoder_target,
    disarm_tension_trigger,
    get_bundle,
    get_delta,
    get_distance,
    get_encoder_target,
    get_imu,
    get_snapshot,
    get_tension_trigger,
    ping,
    save_encoders,
    wait_encoder_target,
    wait_tension_trigger,
)

__all__ = [
    "EvbClient",
    "DeviceError",
    "send_command",
    "ping",
    "arm_encoder_target",
    "arm_tension_trigger",
    "disarm_encoder_target",
    "disarm_tension_trigger",
    "get_bundle",
    "get_delta",
    "get_distance",
    "get_encoder_target",
    "get_imu",
    "get_snapshot",
    "get_tension_trigger",
    "save_encoders",
    "wait_encoder_target",
    "wait_tension_trigger",
]
