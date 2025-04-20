#!/usr/bin/env python3
import gpiod
import time

class Pin:
    """Helper for a single GPIO line using libgpiod."""
    def __init__(self, chip='gpiochip0', offset=0, direction='in', default=0):
        """
        :param chip:      GPIO chip device (e.g. 'gpiochip0')
        :param offset:    line offset (0 for GP80, 1 for GP81, …)
        :param direction: 'in' or 'out'
        :param default:   initial value if direction='out'
        """
        self.line = gpiod.Chip(chip).get_line(offset)
        if direction == 'out':
            self.line.request(
                consumer=f'pin{offset}',
                type=gpiod.LINE_REQ_DIR_OUT,
                default_vals=[default]
            )
        else:
            self.line.request(
                consumer=f'pin{offset}',
                type=gpiod.LINE_REQ_DIR_IN
            )

    def read(self):
        """Read the current value (0 or 1)."""
        return self.line.get_value()

    def write(self, value):
        """Drive the line high (1) or low (0). Only valid if direction='out'."""
        self.line.set_value(int(bool(value)))

    def release(self):
        """Release the line when you’re done."""
        self.line.release()


def main():
    # Example: mirror GP80 to GP81
    input_pin  = Pin(offset=0, direction='in')    # GP80
    output_pin = Pin(offset=1, direction='out', default=0)  # GP81

    print("Reading GP80 and driving GP81 to match. Ctrl+C to quit.")
    try:
        while True:
            val = input_pin.read()
            print(f"GP80 = {val}")
            output_pin.write(val)
            time.sleep(0.2)

    except KeyboardInterrupt:
        print("\nExiting…")

    finally:
        input_pin.release()
        output_pin.release()


if __name__ == "__main__":
    main()
