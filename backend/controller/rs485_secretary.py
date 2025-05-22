# rs485_secretary.py
import threading, queue, time
import serial

def _expected_bytes(fc, count):
    # 0x06 → 8-byte echo; 0x03 → 5 + 2*count + 2 CRC
    return 8 if fc == 0x06 else (5 + 2*count + 2)

class RS485Secretary:
    def __init__(self, port='/dev/ttyUSB0', baud=9600, timeout=1):
        self.ser = serial.Serial(port, baudrate=baud, timeout=timeout)
        self.q   = queue.Queue()
        self.stop= threading.Event()
        self.th  = threading.Thread(target=self._worker, daemon=True)
        self.th.start()

    def send(self, frame: bytes, expected_len: int, callback=None, wait_ack=True):
        """
        frame        = full Modbus RTU bytes (including CRC)
        expected_len = how many reply bytes to read
        callback     = fn(resp_bytes) if you want async handling
        wait_ack     = if False, we don’t block for reply at all
        """
        self.q.put((frame, expected_len, callback, wait_ack))

    def _worker(self):
        while not self.stop.is_set():
            try:
                frame, exp, cb, wait = self.q.get(timeout=0.1)
            except queue.Empty:
                continue

            # clear old data & transmit
            self.ser.reset_input_buffer()
            self.ser.write(frame)

            resp = None
            if wait:
                resp = self.ser.read(exp)

            if cb:
                try: cb(resp)
                except Exception: pass

            self.q.task_done()

    def close(self):
        self.stop.set()
        self.th.join()
        self.ser.close()
