#!/usr/bin/env python3
"""Compatibility shim for web_control."""

from app.web_control import *  # noqa: F401,F403


if __name__ == "__main__":
    raise SystemExit("Run via app/main.py")
