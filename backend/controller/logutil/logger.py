"""Simple structured logger without stdlib logging to avoid name conflicts."""

from __future__ import annotations

import time


class Logger:
    def __init__(self, name: str):
        self.name = name

    def _log(self, level: str, msg: str):
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
