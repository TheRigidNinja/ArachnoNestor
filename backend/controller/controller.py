import time
from motor_controller import MotorController
from imu_client      import IMUClient
from pid             import PID
import asyncio

class ArachnoNestor:
    def __init__(
        self,
        motor_addrs: list[int],
        base_rpm:    int    = 300,
        pid_params:  tuple  = (1.2, 0.01, 0.1),
        pid_limits:  tuple  = (-500, 500)
    ):
        self.motor    = MotorController(addresses=motor_addrs)
        self.imu      = IMUClient()
        self.base_rpm = base_rpm

        kp, ki, kd = pid_params
        mn, mx     = pid_limits
        self.pid   = PID(kp, ki, kd, mn=mn, mx=mx)
        
    def engage_motors_fast(self, rpm: float, forward: bool = True):
        # phase 1: set speed on every motor without blocking
        raw_val = ((int(rpm)&0xFF)<<8) | ((int(rpm)>>8)&0xFF)
        for m in self.motor.addresses:
            self.motor.send_no_ack(m, 0x06, 0x8005, value=raw_val)

        # tiny pause to let DE/RE flip if needed
        time.sleep(0.01)

        # phase 2: send start to all at once
        code = 0x0902 if forward else 0x0B02
        for m in self.motor.addresses:
            self.motor.send_no_ack(m, 0x06, 0x8000, value=code)

    def engage_motor(self, motor_id: int, rpm: float, forward: bool = True):
        """Spin one motor."""
        self.motor.write_rpm(motor_id, rpm)
        self.motor.start(motor_id, forward)

    def engage_motors(self, rpm: float, forward: bool = True):
        """Spin all configured motors."""
        for m in self.motor.addresses:
            self.engage_motor(m, rpm, forward)
            time.sleep(0.1)  # wait for motor to start

            print(f"Motor {m}: {rpm:.0f} RPM {'forward' if forward else 'reverse'}")

    def stop_motor(self, motor_id: int, brake: bool = False):
        """Stop one motor."""
        self.motor.stop(motor_id, brake)

    def stop_all(self, brake: bool = False):
        """Stop all motors."""
        for m in self.motor.addresses:
            self.stop_motor(m, brake)
            time.sleep(0.1)

    def balance_loop(self, sample_hz: float = 20.0):
        """Example PID loop: hold roll at zero by adjusting RPM."""
        period = 1.0 / sample_hz
        self.imu.connect()
        stream     = self.imu.stream()
        self.pid.reset()

        last_time = time.time()
        try:
            for reading in stream:
                now = time.time()
                dt  = now - last_time if now > last_time else period
                last_time = now

                # read representative motor #1
                meas = self.motor.read_speed(self.motor.addresses[0]) or 0.0

                # compute PID correction around base_rpm
                corr = self.pid.update(0.0, reading.roll, dt)
                cmd  = self.base_rpm + corr

                self.engage_motors(cmd)
                print(f"roll={reading.roll:+.2f}°  →  rpm={meas:.0f}  cmd={cmd:.0f}")

                # maintain steady loop rate
                delay = period - (time.time() - now)
                if delay > 0:
                    time.sleep(delay)

        finally:
            self.stop_all(brake=False)
            self.imu.close()
