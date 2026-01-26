from __future__ import annotations

import socket
import time


class LineClient:
    def __init__(self, host: str, port: int, timeout: float, nonblocking: bool = False):
        self.addr = (host, port)
        self.timeout = timeout
        self.nonblocking = nonblocking
        self.sock = None
        self._file = None
        self._buf = bytearray()

    def connect(self):
        self.sock = socket.create_connection(self.addr, self.timeout)
        if self.nonblocking:
            self.sock.setblocking(False)
        else:
            self._file = self.sock.makefile("r")

    def close(self):
        if self._file:
            self._file.close()
            self._file = None
        if self.sock:
            self.sock.close()
            self.sock = None
        self._buf.clear()

    def send(self, data: bytes):
        if self.sock is None:
            raise RuntimeError("Not connected")
        self.sock.sendall(data)

    def readline(self) -> str:
        if self._file is None:
            raise RuntimeError("LineClient not in blocking mode")
        return self._file.readline()

    def recv_line(self, timeout: float = 2.0, chunk_size: int = 1024) -> bytes:
        if self.sock is None:
            raise RuntimeError("Not connected")

        start = time.time()
        while True:
            try:
                chunk = self.sock.recv(chunk_size)
                if not chunk:
                    if self._buf:
                        line = bytes(self._buf)
                        self._buf.clear()
                        return line
                    return b""

                self._buf.extend(chunk)
                if b"\n" in self._buf:
                    idx = self._buf.index(b"\n") + 1
                    line = bytes(self._buf[:idx])
                    del self._buf[:idx]
                    return line
            except BlockingIOError:
                if time.time() - start > timeout:
                    if self._buf:
                        line = bytes(self._buf)
                        self._buf.clear()
                        return line
                    return b""
                time.sleep(0.01)
