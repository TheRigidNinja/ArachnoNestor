#!/usr/bin/env python3
import time
from controller import ArachnoNestor

# Instantiate your robot once with the correct motor addresses
bot = ArachnoNestor(motor_addrs=[1, 2, 3, 4], base_rpm=300)


def manual_mode(rpm: int, motors: list[int], duration: float = None, forward: bool = True):
    bot.engage_selected(rpm, forward, motors)
    if duration is not None:
        time.sleep(duration)
        bot.stop_all(brake=True)


def move_axis(axis: str, direction: str, rpm: int, duration: float):
    if axis.lower() == 'x':
        positive = (direction == '+')
        bot.move_x(rpm, positive)
    elif axis.lower() == 'y':
        positive = (direction == '+')
        bot.move_y(rpm, positive)
    elif axis.lower() == 'z':
        up = (direction == '+')
        bot.move_z(rpm, up)
    else:
        raise ValueError("Invalid axis: choose 'x', 'y', or 'z'")

    time.sleep(duration)
    bot.stop_all(brake=True)


def stabilize(duration: float, sample_hz: float = 20.0, pid_params: tuple[float, float, float] = (2.0, 0.1, 0.5)):
    """
    Balance the platform using IMU feedback for a set duration.

    :param duration: how long to stabilize (seconds)
    :param sample_hz: control loop frequency
    :param pid_params: (kp, ki, kd) gains for roll/pitch PID
    """
    bot.stabilize(duration, sample_hz, pid_params)


# Example standalone sequence:
if __name__ == '__main__':
    # 1) Manual: motors 1 & 4 at 200 RPM for 5s
    manual_mode(200, [1], duration=5)

    # 2) Cartesian move: +X at 300 RPM for 2s
    # move_axis('x', '+', 300, 2)

    # # 3) Cartesian move: -Y at 250 RPM for 4s
    # move_axis('y', '-', 250, 4)

    # # 4) Stabilize for 10s at 30Hz with custom PID
    # stabilize(10, sample_hz=30, pid_params=(2.0, 0.1, 0.5))
