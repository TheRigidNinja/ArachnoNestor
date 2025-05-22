import serial
import time
import crcmod

class MotorController:
    def __init__(
        self,
        port: str = '/dev/ttyUSB0',
        baud: int = 9600,
        addresses: list[int] = None,
        motor_poles_pair: int = 2
    ):
        if addresses is None:
            addresses = [1, 2, 3, 4]
        self.ser = serial.Serial(port, baudrate=baud, timeout=1)
        self.addresses = addresses
        self.motor_poles_pair = motor_poles_pair
        # build CRC-16/MODBUS
        self._crc = crcmod.mkCrcFun(0x18005, rev=True, initCrc=0xFFFF, xorOut=0x0000)

    def _crc_bytes(self, frame: bytes) -> bytes:
        v = self._crc(frame)
        return bytes([v & 0xFF, (v >> 8) & 0xFF])
    
    def _build_frame(self, slave, fc, reg, value=None, count=None) -> bytes:
        frame = bytearray([slave, fc]) + reg.to_bytes(2,'big')
        if fc == 0x06 and value is not None:   frame += value.to_bytes(2,'big')
        if fc == 0x03 and count  is not None:   frame += count.to_bytes(2,'big')
        frame += self._crc_bytes(frame)
        return frame

    def send_no_ack(self, slave:int, fc:int, reg:int, value=None, count=None):
        """Just push a Modbus frame onto RS-485 and return."""
        frame = self._build_frame(slave, fc, reg, value, count)
        # purge old input, in case a previous op left bytes
        self.ser.reset_input_buffer()
        self.ser.write(frame)
        
    # send command to slave
    def _send(self, slave, fc, reg, value=None, count=None):
        frame = bytearray([slave, fc]) + reg.to_bytes(2, 'big')
        if fc == 0x06 and value is not None:
            frame += value.to_bytes(2, 'big')
        if fc == 0x03 and count  is not None:
            frame += count.to_bytes(2, 'big')
        frame += self._crc_bytes(frame)

        # 1) purge any old bytes
        self.ser.reset_input_buffer()

        # 2) send
        print("Command sent✅:", self.print_hex_string(frame.hex()))
        self.ser.write(frame)

        # 3) read exactly what we expect:
        expected = 8 if fc == 0x06 else (5 + 2*count + 2)
        response = self.ser.read(expected)

        if len(response) == expected:
            print("Response received📤:", response.hex())
            return response
        else:
            print(f"No response received🛑  (got {len(response)} bytes)")
            return None

    
    def print_hex_string(self, hex_string: str) -> str:
        """Format hex string for printing."""
        return ' '.join([hex_string[i:i+2] for i in range(0, len(hex_string), 2)])
    

    def write_rpm(self, slave: int, rpm: float) -> bytes:
        """Set target RPM (clamped 0–4000)."""
        r = max(0, min(4000, int(rpm)))
        # controller expects low-byte first in register 0x8005
        raw = ((r & 0xFF) << 8) | ((r >> 8) & 0xFF)

        return self._send(slave, 0x06, 0x8005, value=raw)
    
    def read_speed(self, slave:int):
        resp = self._send(slave, 0x03, 0x8005, count=1)  
        if len(resp)>=7:
            raw = int.from_bytes(resp[3:5], 'little')
            return raw  # read set RPM
        return None
    
    def read_actual_speed(self, slave: int) -> float | None:
        """Read actual RPM from 0x8018."""
        resp = self._send(slave, 0x03, 0x8018, count=1)
        if len(resp) >= 7:
            raw = int.from_bytes(resp[3:5], 'little')
            # (raw * 20) / (pole_pairs * 2)
            return (raw * 20) / (self.motor_poles_pair * 2)
        return None

    def start(self, slave: int, forward: bool = True) -> bytes:
        """Spin motor forward or reverse."""
        code = 0x0902 if forward else 0x0B02
        return self._send(slave, 0x06, 0x8000, value=code)

    def stop(self, slave: int, brake: bool = False) -> bytes:
        """Natural (0x0A02) or braking (0x0D02) stop."""
        code = 0x0D02 if brake else 0x0802

        print(f"Stopping motor {slave} with code {code:#06x}")
        return self._send(slave, 0x06, 0x8000, value=code)

    def stop_all(self, brake: bool = False):
        """
        Stop each motor in sequence. Waits for each motor's echo
        before moving on. Logs a warning if no echo arrives.
        """
        for m in self.addresses:
            resp = self.stop(m, brake)
            if resp is None:
                print(f"Warning: no response from motor {m} after stop command")
            else:
                print(f"Motor {m} stopped with response: {resp.hex()}")
            # time.sleep(0.07)
            # dt = (time.time() - t0) * 1000  # ms
            

    def set_torque(self, slave: int, start_torque: int, sensorless_speed: int) -> bytes:
        val = (start_torque << 8) | sensorless_speed
        return self._send(slave, 0x06, 0x8002, value=val)

    def set_accel(self, slave: int, accel_t: int, decel_t: int) -> bytes:
        val = (accel_t << 8) | decel_t
        return self._send(slave, 0x06, 0x8003, value=val)

    def set_current(self, slave: int, cont_current: int, type_flag: int) -> bytes:
        val = (cont_current << 8) | type_flag
        return self._send(slave, 0x06, 0x8004, value=val)

    def set_brake_torque(self, slave: int, brake_val: int) -> bytes:
        return self._send(slave, 0x06, 0x8006, value=brake_val)

    def set_address(self, slave: int, new_addr: int) -> bytes:
        """Reprogram the site address (1–250)."""
        return self._send(slave, 0x06, 0x8007, value=new_addr)




