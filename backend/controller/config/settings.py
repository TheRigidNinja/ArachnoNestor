"""Config loader with safe defaults."""

from __future__ import annotations

import ast
import os
import copy
from typing import Any, Dict


DEFAULTS: Dict[str, Any] = {
    "evb": {"host": "192.168.2.123", "port": 5000, "timeout": 2.0},
    "motion": {
        "hall_threshold": 1500,
        "poll_interval": 0.05,
        "stale_timeout": 1.5,
        "evb_backoff_initial": 0.2,
        "evb_backoff_max": 2.0,
        "evb_backoff_factor": 1.5,
        "use_bundle": True,
        "use_power": True,
        "use_imu": True,
        "winch_ids": [1, 2, 3, 4],
        "serial_port": "/dev/ttyUSB0",
        "baud_rate": 9600,
        "device_address": 1,
    },
    "web": {"host": "0.0.0.0", "port": 8080},
    "imu_json": {"host": "192.168.2.123", "port": 5000, "timeout": 5.0},
}


def _simple_yaml_load(text: str) -> Dict[str, Any]:
    data: Dict[str, Any] = {}
    current_section = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        if not line.startswith(" ") and line.endswith(":"):
            current_section = line[:-1].strip()
            data[current_section] = {}
            continue
        if ":" in line and current_section is not None:
            key, val = line.split(":", 1)
            key = key.strip()
            val = val.strip()
            if val == "":
                continue
            try:
                parsed = ast.literal_eval(val)
            except Exception:
                parsed = val.strip('"').strip("'")
            data[current_section][key] = parsed
    return data


def load_config(path: str | None = None) -> Dict[str, Any]:
    path = path or os.path.join(os.path.dirname(__file__), "config.yaml")
    cfg = copy.deepcopy(DEFAULTS)
    if not os.path.exists(path):
        return cfg
    try:
        import yaml  # type: ignore
        with open(path, "r", encoding="utf-8") as f:
            loaded = yaml.safe_load(f) or {}
    except Exception:
        with open(path, "r", encoding="utf-8") as f:
            loaded = _simple_yaml_load(f.read())
    for k, v in loaded.items():
        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
            cfg[k].update(v)
        else:
            cfg[k] = v
    return cfg
