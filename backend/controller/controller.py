import time
from motor_controller import MotorController
from imu_client    import IMUClient
from pid           import PID

class ArachnoNestor:
    def __init__(
        self,
        motor_addrs,
        base_rpm: int = 300,
        pid_params: tuple = (1.2, 0.01, 0.1),
        pid_limits: tuple = (-500,500)
    ):
        self.motor    = MotorController(addresses=motor_addrs)
        self.imu      = IMUClient()
        self.base_rpm = base_rpm

        kp, ki, kd = pid_params
        mn, mx     = pid_limits
        self.pid   = PID(kp, ki, kd, mn=mn, mx=mx)

    def engage_motors(self, rpm: float):
        """Write & start on all addresses."""
        for addr in self.motor.addresses:
            self.motor.write_rpm(addr, int(rpm))
            self.motor.start(addr, forward=True)

    def stop_all(self, brake: bool = False):
        for addr in self.motor.addresses:
            self.motor.stop(addr, brake)

    def balance_loop(self, sample_hz: float = 20.0):
        period = 1.0 / sample_hz
        self.imu.connect()
        stream = self.imu.stream()
        self.pid.reset()

        last_time = time.time()
        try:
            for reading in stream:
                now    = time.time()
                dt     = now - last_time if now > last_time else period
                last_time = now

                roll = reading.roll  # current tilt

                # PID: setpoint = 0° roll
                corr_rpm = self.pid.update(0.0, roll, dt)
                cmd_rpm  = self.base_rpm + corr_rpm

                self.engage_motors(cmd_rpm)
                print(f"roll={roll:+.2f}°  →  cmd RPM={cmd_rpm:.0f}")

                # maintain constant loop rate
                sleep = period - (time.time() - now)
                if sleep > 0:
                    time.sleep(sleep)

        finally:
            self.stop_all(brake=False)
            self.imu.close()


if __name__ == "__main__":
    bot = ArachnoNestor([1,2,3,4], base_rpm=1200)
    bot.balance_loop(20)
