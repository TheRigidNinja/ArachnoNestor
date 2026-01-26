#!/usr/bin/env python3
"""Thin launcher for controller app."""

from controller.config.settings import load_config
from controller.logutil.logger import get_logger
from controller.app.main import run


def main():
    load_config()
    get_logger("main")
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
