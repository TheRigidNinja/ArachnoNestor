"""Minimal config loader with defaults for EVB skeleton."""

from __future__ import annotations

import ast
import os
from typing import Any, Dict


DEFAULTS: Dict[str, Any] = {
    "evb": {"host": "192.168.2.123", "port": 5000, "timeout": 1.0},
    "motion": {"winch_ids": [1, 2, 3, 4], "poll_interval": 0.05},
    "web": {"host": "0.0.0.0", "port": 8080},
    "ui": {"stale_threshold_ms": 1000},
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
    cfg = {k: v.copy() for k, v in DEFAULTS.items()}
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
