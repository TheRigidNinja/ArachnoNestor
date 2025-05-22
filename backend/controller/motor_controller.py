import time
import serial
import crcmod

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
        # Open serial port (half-duplex RS-485)
        self.ser = serial.Serial(port, baudrate=baud, timeout=0.01)
        # If RTS is wired to DE, start in receive mode
        try:
            self.ser.setRTS(False)
        except Exception:
            pass

        self.addresses        = addresses
        self.motor_poles_pair = motor_poles_pair
        # CRC-16/MODBUS generator
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
        """Build a Modbus RTU frame with CRC."""
        frame = bytearray([slave, fc]) + reg.to_bytes(2, 'big')
        if fc == 0x06 and value is not None:
            frame += value.to_bytes(2, 'big')
        if fc == 0x03 and count is not None:
            frame += count.to_bytes(2, 'big')
        frame += self._crc_bytes(frame)
        return frame

    def _send_and_recv(
        self,
        frame: bytes,
        slave: int,
        fc: int,
        timeout: float = 0.2
    ) -> bytes | None:
        """
        Send one Modbus frame and wait for its 8-byte echo.
        Returns the raw echo or None on timeout.
        """
        # 1) clear input
        self.ser.reset_input_buffer()
        # 2) assert DE if available
        try:
            self.ser.setRTS(True)
        except Exception:
            pass
        # 3) send and flush
        self.ser.write(frame)
        self.ser.flush()
        # 4) deactivate DE → receive
        try:
            self.ser.setRTS(False)
        except Exception:
            pass

        buf = bytearray()
        deadline = time.time() + timeout
        # read until we find a valid echo or timeout
        while time.time() < deadline:
            chunk = self.ser.read(16)
            if chunk:
                buf.extend(chunk)
                # look for 8-byte sequence starting with slave,fc
                for i in range(len(buf) - 7):
                    if buf[i] == slave and buf[i+1] == fc:
                        return bytes(buf[i:i+8])
            else:
                time.sleep(0.005)
        return None
    
    def _safe_send(self, frame, slave, fc, tries=4, timeout=0.2):
        for i in range(tries):
            print(f"⏳ Sending frame {i+1}/{tries} to slave {slave}...")
            resp = self._send_and_recv(frame, slave, fc, timeout=timeout)
            if resp:
                return resp
            
        return None
    
    # --- Core commands ---
    def write_rpm(self, slave: int, rpm: float) -> bytes | None:
        """Set target RPM (0–4000)."""
        r = max(0, min(4000, int(rpm)))
        raw = ((r & 0xFF) << 8) | (r >> 8)
        frame = self._build_frame(slave, 0x06, 0x8005, value=raw)
        return self._safe_send(frame, slave, 0x06)

    def read_speed(self, slave: int) -> float | None:
        """Read actual RPM from register 0x8018."""
        frame = self._build_frame(slave, 0x03, 0x8018, count=1)
        resp = self._safe_send(frame, slave, 0x03)
        if resp and len(resp) >= 7:
            raw = int.from_bytes(resp[3:5], 'little')
            return (raw * 20) / (self.motor_poles_pair * 2)
        return None

    def start(self, slave: int, forward: bool = True) -> bytes | None:
        """Spin motor forward or reverse."""
        code = 0x0902 if forward else 0x0B02
        frame = self._build_frame(slave, 0x06, 0x8000, value=code)
        return self._safe_send(frame, slave, 0x06)

    def stop(self, slave: int, brake: bool = False) -> bytes | None:
        """Natural (0x0A02) or brake (0x0D02) stop for one motor."""
        code = 0x0D02 if brake else 0x0A02
        frame = self._build_frame(slave, 0x06, 0x8000, value=code)
        return self._safe_send(frame, slave, 0x06)

    # --- Batch stop ---

    def stop_all(self, brake: bool = False):
        """
        Stop each motor in sequence, waiting for its echo before next.
        """
        for m in self.addresses:
            t0 = time.time()
            resp = self.stop(m, brake)
            dt = (time.time() - t0) * 1000
            if resp:
                print(f"✅ Motor {m} stopped (ack in {dt:.0f} ms): {resp.hex()}")
            else:
                print(f"⚠️  Motor {m} no ACK after {dt:.0f} ms")

    # --- Additional setters (optional) ---

    def set_torque(self, slave: int, start_torque: int, sensorless_speed: int) -> bytes | None:
        val = (start_torque << 8) | sensorless_speed
        frame = self._build_frame(slave, 0x06, 0x8002, value=val)
        return self._safe_send(frame, slave, 0x06)

    def set_accel(self, slave: int, accel_t: int, decel_t: int) -> bytes | None:
        val = (accel_t << 8) | decel_t
        frame = self._build_frame(slave, 0x06, 0x8003, value=val)
        return self._safe_send(frame, slave, 0x06)

    def set_current(self, slave: int, cont_current: int, type_flag: int) -> bytes | None:
        val = (cont_current << 8) | type_flag
        frame = self._build_frame(slave, 0x06, 0x8004, value=val)
        return self._safe_send(frame, slave, 0x06)

    def set_brake_torque(self, slave: int, brake_val: int) -> bytes | None:
        frame = self._build_frame(slave, 0x06, 0x8006, value=brake_val)
        return self._safe_send(frame, slave, 0x06)

    def set_address(self, slave: int, new_addr: int) -> bytes | None:
        frame = self._build_frame(slave, 0x06, 0x8007, value=new_addr)
        return self._safe_send(frame, slave, 0x06)
