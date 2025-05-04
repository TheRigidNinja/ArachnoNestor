#!/usr/bin/env python3
import subprocess, time

for off in range(56, 64):
    pin = 80 + (off - 56)
    print(f"Testing GP{pin} (offset {off})…")
    subprocess.run(['gpioset', 'gpiochip0', f'{off}=1'])
    time.sleep(0.5)
    subprocess.run(['gpioset', 'gpiochip0', f'{off}=0'])
    time.sleep(0.2)
