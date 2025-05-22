import time
import threading
import crcmod
from rs485_secretary import RS485Secretary

class MotorController:
    def __init__(
        self,
        port: str = '/dev/ttyUSB0',
        baud: int = 9600,
        addresses: list[int] | None = None,
        motor_poles_pair: int = 2
    ):
        if addresses is None:
            addresses = [1, 2, 3, 4]
        # Single-threaded RS-485 manager
        self.bus              = RS485Secretary(port=port, baud=baud, timeout=1)
        self.addresses        = addresses
        self.motor_poles_pair = motor_poles_pair
        # CRC-16/MODBUS function
        self._crc = crcmod.mkCrcFun(
            0x18005, rev=True, initCrc=0xFFFF, xorOut=0x0000
        )

    def _crc_bytes(self, frame: bytes) -> bytes:
        v = self._crc(frame)
        return bytes([v & 0xFF, v >> 8])

    def _build_frame(
        self,
        slave: int,
        fc: int,
        reg: int,
        value: int | None = None,
        count: int | None = None
    ) -> bytes:
        # Build Modbus RTU frame
        frame = bytearray([slave, fc]) + reg.to_bytes(2, 'big')
        if fc == 0x06 and value is not None:
            frame += value.to_bytes(2, 'big')
        if fc == 0x03 and count is not None:
            frame += count.to_bytes(2, 'big')
        frame += self._crc_bytes(frame)
        return frame

    def _send(
        self,
        slave: int,
        fc: int,
        reg: int,
        value: int | None = None,
        count: int | None = None,
        timeout: float | None = None
    ) -> bytes | None:
        """
        Send a frame and block until we get the expected reply or timeout.
        Returns the raw reply bytes or None.
        """
        frame    = self._build_frame(slave, fc, reg, value, count)
        expected = 8 if fc == 0x06 else (5 + 2*count + 2)

        # Use an Event + callback to wait for the secretary’s reply
        result = {}
        evt    = threading.Event()

        def _cb(resp: bytes | None):
            result['resp'] = resp
            evt.set()

        # Fire it off
        self.bus.send(frame, expected, callback=_cb, wait_ack=True)

        # Wait up to serial timeout (or override)
        wait_t = timeout if timeout is not None else self.bus.ser.timeout
        evt.wait(wait_t)

        return result.get('resp', None)

    # ——— Core motor commands ——— #

    def write_rpm(self, slave: int, rpm: float) -> bytes | None:
        """Set target RPM (0–4000)."""
        r   = max(0, min(4000, int(rpm)))
        raw = ((r & 0xFF) << 8) | (r >> 8)
        return self._send(slave, 0x06, 0x8005, value=raw)

    def read_speed(self, slave: int) -> float | None:
        """Read actual RPM from register 0x8018."""
        resp = self._send(slave, 0x03, 0x8018, count=1)
        if resp and len(resp) >= 7:
            raw = int.from_bytes(resp[3:5], 'little')
            return (raw * 20) / (self.motor_poles_pair * 2)
        return None

    def start(self, slave: int, forward: bool = True) -> bytes | None:
        """Spin motor forward or reverse."""
        code = 0x0902 if forward else 0x0B02
        return self._send(slave, 0x06, 0x8000, value=code)

    def stop(self, slave: int, brake: bool = False) -> bytes | None:
        """Natural (0x0A02) or brake (0x0D02) stop—waits for echo."""
        code = 0x0D02 if brake else 0x0A02
        return self._send(slave, 0x06, 0x8000, value=code)

    # ——— Batch helpers ——— #

    def stop_all(self, brake: bool = False):
        """
        Stop each motor in sequence, waiting for its Modbus echo
        before moving on. Logs a warning if no echo arrives.
        """
        for m in self.addresses:
            t0   = time.time()
            resp = self.stop(m, brake)
            dt   = (time.time() - t0) * 1000  # ms
            if resp:
                print(f"✅ Motor {m} stopped (ack in {dt:.0f} ms): {resp.hex()}")
            else:
                print(f"⚠️  Motor {m} no ACK after {dt:.0f} ms – moving on")

    # ——— Extra configuration ——— #

    def set_torque(
        self, slave: int, start_torque: int, sensorless_speed: int
    ) -> bytes | None:
        val = (start_torque << 8) | sensorless_speed
        return self._send(slave, 0x06, 0x8002, value=val)

    def set_accel(
        self, slave: int, accel_t: int, decel_t: int
    ) -> bytes | None:
        val = (accel_t << 8) | decel_t
        return self._send(slave, 0x06, 0x8003, value=val)

    def set_current(
        self, slave: int, cont_current: int, type_flag: int
    ) -> bytes | None:
        val = (cont_current << 8) | type_flag
        return self._send(slave, 0x06, 0x8004, value=val)

    def set_brake_torque(self, slave: int, brake_val: int) -> bytes | None:
        return self._send(slave, 0x06, 0x8006, value=brake_val)

    def set_address(self, slave: int, new_addr: int) -> bytes | None:
        return self._send(slave, 0x06, 0x8007, value=new_addr)
