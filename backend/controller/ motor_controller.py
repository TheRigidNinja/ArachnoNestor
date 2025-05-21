import serial, time, crcmod

class MotorController:
    def __init__(self, port='/dev/ttyUSB0', baud=9600, addresses=[1,2,3,4]):
        self.ser = serial.Serial(port, baudrate=baud, timeout=1)
        self.addresses = addresses
        self._crc = crcmod.mkCrcFun(0x18005, rev=True, initCrc=0xFFFF, xorOut=0x0000)

    def _crc_bytes(self, frame: bytes) -> bytes:
        v = self._crc(frame)
        return bytes([v & 0xFF, v >> 8])

    def _send(self, slave, fc, reg, value=None, count=None):
        frame = bytearray([slave, fc]) + reg.to_bytes(2,'big')
        if fc==0x06 and value is not None:   frame += value.to_bytes(2,'big')
        if fc==0x03 and count is not None:   frame += count.to_bytes(2,'big')
        frame += self._crc_bytes(frame)
        self.ser.write(frame)
        time.sleep(0.05)
        return self.ser.read_all()

    # Core functions:
    
    # START / STOP
    def start(self, slave:int, forward:bool=True):
        code = 0x0902 if forward else 0x0B02
        return self._send(slave, 0x06, 0x8000, value=code)

    def stop(self, slave:int, brake:bool=False):
        code = 0x0D02 if brake else 0x0A02  # GUI “Stop command” uses 0x0A02
        return self._send(slave, 0x06, 0x8000, value=code)

    # speed
    def set_speed(self, slave:int, rpm:int):
        rpm = max(0, min(4000, rpm))
        raw = ((rpm & 0xFF)<<8) | (rpm>>8)
        return self._send(slave, 0x06, 0x8005, value=raw)

    def read_speed(self, slave:int):
        resp = self._send(slave, 0x03, 0x8018, count=1)
        if len(resp)>=7:
            raw = int.from_bytes(resp[3:5], 'little')
            return raw  # actual RPM reading
        return None

    # torque & timing
    def set_torque(self, slave:int, start_torque:int, sensorless_speed:int):
        val = (start_torque<<8) | sensorless_speed
        return self._send(slave, 0x06, 0x8002, value=val)

    def set_accel(self, slave:int, accel_t:int, decel_t:int):
        val = (accel_t<<8) | decel_t
        return self._send(slave, 0x06, 0x8003, value=val)

    # current & type
    def set_current(self, slave:int, cont_current:int, type_flag:int):
        val = (cont_current<<8) | type_flag
        return self._send(slave, 0x06, 0x8004, value=val)

    def set_brake_torque(self, slave:int, brake_val:int):
        return self._send(slave, 0x06, 0x8006, value=brake_val)

    def set_address(self, slave:int, new_addr:int):
        # you must first talk to old slave, then write 0x8007
        return self._send(slave, 0x06, 0x8007, value=new_addr)