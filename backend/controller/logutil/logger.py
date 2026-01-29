"""Simple structured logger without stdlib logging to avoid name conflicts."""

from __future__ import annotations

import os
import time

from config.settings import load_config

_CONFIG = load_config()
_LOG_CFG = _CONFIG.get("log", {}) if isinstance(_CONFIG, dict) else {}
_LOG_MODE = os.getenv("LOG_MODE", str(_LOG_CFG.get("mode", "all"))).lower()
_ALLOW_MODULES = tuple(_LOG_CFG.get("allow_modules", []) or [])
_ALLOW_PREFIXES = tuple(_LOG_CFG.get("allow_prefixes", []) or [])


def _is_allowed(name: str, msg: str) -> bool:
    if _LOG_MODE in ("off", "none"):
        return False
    if _LOG_MODE not in ("ui_only", "ui"):
        return True
    for mod in _ALLOW_MODULES:
        if name == mod or name.startswith(f"{mod}."):
            return True
    for prefix in _ALLOW_PREFIXES:
        if msg.startswith(prefix):
            return True
    return False


class Logger:
    def __init__(self, name: str):
        self.name = name

    def _log(self, level: str, msg: str):
        if not _is_allowed(self.name, msg):
            return
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        print(f"{ts} [{level}] {self.name}: {msg}")

    def info(self, msg: str):
        self._log("INFO", msg)

    def warning(self, msg: str):
        self._log("WARN", msg)

    def error(self, msg: str):
        self._log("ERROR", msg)

    def debug(self, msg: str):
        self._log("DEBUG", msg)


def get_logger(name: str) -> Logger:
    return Logger(name)
