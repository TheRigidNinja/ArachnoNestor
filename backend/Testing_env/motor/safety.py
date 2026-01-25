"""Safety rules and watchdogs for motion control."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict, Optional


@dataclass
class SafetyStatus:
    can_move: bool
    reason: Optional[str] = None


class SafetyMonitor:
    def __init__(self, hall_threshold: int, stale_timeout_s: float):
        self.hall_threshold = hall_threshold
        self.stale_timeout_s = stale_timeout_s

    def evaluate(self, halls: Dict[int, int], last_update: Optional[float]) -> SafetyStatus:
        if last_update is None:
            return SafetyStatus(False, "no sensor update")
        if time.time() - last_update > self.stale_timeout_s:
            return SafetyStatus(False, "stale sensor data")
        if not halls:
            return SafetyStatus(False, "missing hall data")
        if any(v < self.hall_threshold for v in halls.values()):
            return SafetyStatus(False, f"hall below {self.hall_threshold}")
        return SafetyStatus(True, None)
