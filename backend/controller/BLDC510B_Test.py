# import gpiod
# import threading
# import time

# class PulseCounter(threading.Thread):
#     def __init__(self, chip='gpiochip0', line_offset=0):
#         super().__init__(daemon=True)
#         self.chip = gpiod.Chip(chip)
#         self.line = self.chip.get_line(line_offset)
#         self.line.request(consumer='pulse_counter',
#                           type=gpiod.LINE_REQ_EV_RISING_EDGE)
#         self.count = 0
#         self._stop = threading.Event()

#     def run(self):
#         while not self._stop.is_set():
#             event = self.line.event_read()  # blocks until a pulse
#             if event.type == gpiod.LineEvent.RISING_EDGE:
#                 self.count += 1

#     def stop(self):
#         self._stop.set()
#         self.line.release()


import gpiod

# Open the GPIO chip
chip = gpiod.Chip('gpiochip0')

# Grab line offset 0 (GP80) and request it as an input
line = chip.get_line(0)
line.request(consumer='read_pin0', type=gpiod.LINE_REQ_DIR_IN)

# Read & print
value = line.get_value()
print(f"GPIO 0 (GP80) = {value}")  # 0 => low, 1 => high
