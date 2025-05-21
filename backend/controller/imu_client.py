import socket, json
from dataclasses import dataclass

@dataclass
class IMUReading:
    seq: int; ts:int; yaw:float; pitch:float; roll:float
    accel:list; gyro:list; mag:list; pressure:float; temp:float

class IMUClient:
    def __init__(self, host='192.168.2.123', port=5000, timeout=5):
        self.addr=(host,port); self.timeout=timeout; self.sock=None; self.fd=None

    def connect(self):
        self.sock=socket.create_connection(self.addr,self.timeout)
        self.fd=self.sock.makefile('r')
        print(f"✅ Connected to {self.addr[0]}:{self.addr[1]}")

    def close(self):
        if self.fd: self.fd.close()
        if self.sock: self.sock.close()
        print("🔌 Disconnected")

    def stream(self):
        while True:
            line=self.fd.readline()
            if not line: break
            d=json.loads(line)
            yield IMUReading(
                seq=int(d['seq']), ts=int(d['ts']),
                yaw=float(d['yaw']), pitch=float(d['pitch']), roll=float(d['roll']),
                accel=d['accel'], gyro=d['gyro'], mag=d['mag'],
                pressure=float(d['pressure']), temp=float(d['temp'])
            )
