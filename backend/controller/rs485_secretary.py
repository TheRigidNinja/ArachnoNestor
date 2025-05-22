import threading, queue, time
import serial

class RS485Secretary:
    """
    Serializes RS-485 traffic on a half-duplex bus.
    Ensures proper DE/RE toggling (via RTS) and inter-frame delays.
    """
    def __init__(
        self,
        port: str = '/dev/ttyUSB0',
        baud: int = 9600,
        timeout: float = 1.0
    ):
        """
        Initialize serial port and start worker thread.
        """
        self.ser = serial.Serial(port, baudrate=baud, timeout=timeout)
        # Ensure RTS starts low (receive mode) if RTS->DE is wired
        try:
            self.ser.setRTS(False)
        except Exception:
            pass

        self.q = queue.Queue()
        self.stop_event = threading.Event()
        self.worker_thread = threading.Thread(target=self._worker, daemon=True)
        self.worker_thread.start()

    def send(
        self,
        frame: bytes,
        expected_len: int,
        callback=None,
        wait_ack: bool = True
    ):
        """
        Enqueue a Modbus RTU frame to send:
         - frame: full bytes including CRC
         - expected_len: number of reply bytes to read
         - callback(resp_bytes) if provided
         - wait_ack: whether to block for reply
        """
        self.q.put((frame, expected_len, callback, wait_ack))

    def _worker(self):
        """
        Worker thread: pops frames, toggles DE via RTS, writes,
        waits for reply, and invokes callback.
        """
        while not self.stop_event.is_set():
            try:
                frame, exp, cb, wait = self.q.get(timeout=0.1)
            except queue.Empty:
                continue

            # Purge any stale bytes
            self.ser.reset_input_buffer()

            # TX mode: assert RTS if available
            try:
                self.ser.setRTS(True)
            except Exception:
                pass

            # Transmit the frame
            self.ser.write(frame)
            self.ser.flush()

            # Bus turnaround: wait for char-time * len + margin
            char_time = (1 / self.ser.baudrate) * 10  # approx sec per byte
            time.sleep(char_time * len(frame) + 1)

            # RX mode: deassert RTS
            try:
                self.ser.setRTS(False)
            except Exception:
                pass

            # Read reply if requested
            resp = None
            if wait:
                resp = self.ser.read(exp)

            # Invoke callback
            if cb:
                try:
                    cb(resp)
                except Exception:
                    pass

            self.q.task_done()
