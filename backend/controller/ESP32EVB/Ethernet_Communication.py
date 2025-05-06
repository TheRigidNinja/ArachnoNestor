#!/usr/bin/env python3
import socket
import json
import time
import math
from contextlib import contextmanager
from dataclasses import dataclass

ESP_IP = "192.168.2.123"  # Must match ESP32's static IP
ESP_PORT = 5000            # Must match ESP32's TCP port
CONNECTION_TIMEOUT = 5.0   # Timeout in seconds

@dataclass
class PIDConfig:
    kp: float = 0.5
    ki: float = 0.01
    kd: float = 0.1
    integral_max: float = 100.0
    output_max: float = 100.0

class ESP32MotorController:
    def __init__(self, pulses_per_meter=1000):
        """Initialize controller with calibration constant"""
        self.sock = None
        self.pulses_per_meter = pulses_per_meter  # Pulses per meter of movement
        
    def connect(self):
        """Establish connection to ESP32 using create_connection"""
        try:
            self.sock = socket.create_connection(
                (ESP_IP, ESP_PORT),
                timeout=CONNECTION_TIMEOUT
            )
            self.sock.setblocking(False)
            print(f"✅ Connected to ESP32 at {ESP_IP}:{ESP_PORT}")
            return True
        except socket.timeout:
            print("⌛ Connection timed out")
            return False
        except ConnectionRefusedError:
            print("❌ Connection refused - is the ESP32 server running?")
            return False
        except Exception as e:
            print(f"⚠️ Connection failed: {type(e).__name__}: {e}")
            return False

    def _ensure_connection(self):
        """Ensure we have an active connection"""
        if self.sock is None:
            if not self.connect():
                raise RuntimeError("No active connection to ESP32")

    def send_command(self, command):
        """Send JSON command and wait for response"""
        self._ensure_connection()
        
        try:
            data = (json.dumps(command) + "\n").encode()
            self.sock.sendall(data)
            return self._recv_response()
        except socket.timeout:
            print("⌛ Response timeout")
            return None
        except Exception as e:
            print(f"⚠️ Communication error: {type(e).__name__}: {e}")
            self.close()
            return None

    def _recv_response(self, timeout=2.0):
        """Receive response with timeout handling"""
        response = b""
        start_time = time.time()
        
        while True:
            try:
                chunk = self.sock.recv(1024)
                if not chunk:
                    break
                response += chunk
                if b"\n" in response:
                    break
            except BlockingIOError:
                if time.time() - start_time > timeout:
                    raise socket.timeout()
                time.sleep(0.01)
                continue
                
        return json.loads(response.decode().strip()) if response else None

    @contextmanager
    def connection(self):
        """Context manager for connection handling"""
        try:
            self.connect()
            yield self
        finally:
            self.close()

    def close(self):
        """Close connection cleanly"""
        if self.sock:
            try:
                self.sock.close()
            except:
                pass
            finally:
                self.sock = None

    # Basic Motor Control
    def control_motor(self, motor_id, enable=None, direction=None, pwm=None):
        """Direct motor control"""
        command = {
            "motors": [{
                "id": motor_id,
                "enable": enable if enable is not None else False,
                "direction": "forward" if direction else "reverse",
                "pwm": pwm if pwm is not None else 0
            }]
        }
        return self.send_command(command)

    def get_pulses(self, motor_id=None):
        """Get pulse counts"""
        if motor_id is not None:
            command = {"motors": [{"id": motor_id, "pulses": True}]}
        else:
            command = {"motors": [{"id": i, "pulses": True} for i in range(4)]}
            
        return self.send_command(command)

    # Closed-Loop Position Control
    def set_pid(self, motor_id, pid_config):
        """Configure PID parameters for a motor"""
        return self.send_command({
            "motors": [{
                "id": motor_id,
                "pid": {
                    "kp": pid_config.kp,
                    "ki": pid_config.ki,
                    "kd": pid_config.kd
                }
            }]
        })

    def move_to_position(self, motor_id, position_meters, tolerance=0.01, timeout=10.0):
        """Move motor to absolute position (meters)"""
        target_pulses = int(position_meters * self.pulses_per_meter)
        tolerance_pulses = int(tolerance * self.pulses_per_meter)
        
        response = self.send_command({
            "motors": [{
                "id": motor_id,
                "target": target_pulses
            }]
        })
        
        if response is None:
            return False

        # Wait until position is reached
        start_time = time.time()
        while time.time() - start_time < timeout:
            current = self.get_pulses(motor_id)
            if current is None:
                return False
                
            error = abs(target_pulses - current)
            if error <= tolerance_pulses:
                return True
                
            time.sleep(0.1)
        
        return False

    def move_distance(self, motor_id, distance_meters, **kwargs):
        """Move motor relative distance (meters)"""
        current = self.get_pulses(motor_id)
        if current is None:
            return False
            
        target_position = (current / self.pulses_per_meter) + distance_meters
        return self.move_to_position(motor_id, target_position, **kwargs)

    def calibrate_pulses_per_meter(self, motor_id, test_distance=0.1):
        """Automatically determine pulses per meter"""
        print(f"Calibrating motor {motor_id}...")
        
        # Move forward known distance
        start = self.get_pulses(motor_id)
        if start is None:
            return False
            
        self.control_motor(motor_id, enable=True, direction=True, pwm=50)
        time.sleep(2)  # Move for fixed time
        self.control_motor(motor_id, enable=False)
        
        end = self.get_pulses(motor_id)
        if end is None:
            return False
            
        pulses = end - start
        self.pulses_per_meter = pulses / test_distance
        print(f"Calibration complete: {self.pulses_per_meter:.1f} pulses/meter")
        return True

def main():
    # Example usage
    with ESP32MotorController().connection() as ctrl:
        if not ctrl.sock:
            return
            
        # 1. Calibration (run once)
        # ctrl.calibrate_pulses_per_meter(0)
        
        # 2. Configure PID (tune these values)
        pid_config = PIDConfig(kp=0.8, ki=0.05, kd=0.2)
        ctrl.set_pid(0, pid_config)
        
        # 3. Basic motor test
        print("Basic motor test:")
        for i in range(3):
            print(ctrl.control_motor(0, enable=True, direction=i%2, pwm=30))
            time.sleep(1)
            print(ctrl.control_motor(0, enable=False))
            print("Pulses:", ctrl.get_pulses(0))
            time.sleep(1)
        
        # 4. Closed-loop position control
        print("\nPosition control test:")
        print("Moving to 0.5m...")
        if ctrl.move_to_position(0, 0.5):
            print("Position reached!")
        else:
            print("Failed to reach position")
        
        print("Current position:", ctrl.get_pulses(0)[0]/ctrl.pulses_per_meter, "meters")

if __name__ == "__main__":
    main()