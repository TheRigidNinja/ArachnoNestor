#!/usr/bin/env python3
import time
import signal
import sys

from controller import ArachnoNestor

# 1) Create your robot once
bot = ArachnoNestor(motor_addrs=[1,2,3,4], base_rpm=300)

# 2) Install a global Ctrl–C handler
def _sigint_handler(signum, frame):
    print("\n🛑 Caught SIGINT — stopping all motors")
    bot.stop_all(brake=True)
    sys.exit(1)

signal.signal(signal.SIGINT, _sigint_handler)


def manual_mode(rpm: int, motors: list[int], duration: float = None, forward: bool = True):
    bot.engage_selected(rpm, forward, motors)
    if duration is not None:
        try:
            time.sleep(duration)
        except KeyboardInterrupt:
            # in case someone hits Ctrl–C during the sleep
            bot.stop_all(brake=True)
            raise
        bot.stop_all(brake=True)


def move_axis(axis: str, direction: str, rpm: int, duration: float):
    positive = direction == '+'
    if axis.lower() == 'x':
        bot.move_x(rpm, positive)
    elif axis.lower() == 'y':
        bot.move_y(rpm, positive)
    elif axis.lower() == 'z':
        bot.move_z(rpm, positive)
    else:
        raise ValueError("Invalid axis: choose 'x', 'y', or 'z'")

    try:
        time.sleep(duration)
    except KeyboardInterrupt:
        bot.stop_all(brake=True)
        raise
    bot.stop_all(brake=True)


def stabilize(duration: float, sample_hz: float = 20.0, pid_params=(2.0,0.1,0.5)):
    try:
        bot.stabilize(duration, sample_hz, pid_params)
    except KeyboardInterrupt:
        # If you hit Ctrl–C mid-stabilize
        bot.stop_all(brake=True)
        raise


if __name__ == '__main__':
    # Example sequence:
    # RPM -- MOTORS -- DURATION (s) -- FORWARD
    manual_mode(200, [1,4], duration=5, forward=True)
    # move_axis('x', '+', 300, 2)
    # move_axis('y', '-', 250, 4)
    # stabilize(10, sample_hz=30, pid_params=(2.0,0.1,0.5))
