import time
from motor_controller import MotorController
from imu_client import IMUClient
from pid import PID

class ArachnoNestor:
    def __init__(
        self,
        motor_addrs: list[int],
        base_rpm: int = 300,
        pid_params: tuple[float, float, float] = (1.2, 0.01, 0.1),
        pid_limits: tuple[float, float] = (-500, 500)
    ):
        # Core subsystems
        self.motor = MotorController(addresses=motor_addrs)
        self.imu   = IMUClient()
        self.base_rpm = base_rpm

        # PID for balancing
        kp, ki, kd = pid_params
        mn, mx     = pid_limits
        self.pid   = PID(kp, ki, kd, mn=mn, mx=mx)

    def engage_motors(self, rpm: float, forward: bool = True):
        """
        Sequentially send a set-RPM then start command to each motor,
        waiting for each acknowledgement (no artificial sleeps).
        """
        for m in self.motor.addresses:
            # 1) set speed
            # time.sleep(0.1)
            ack = self.motor.write_rpm(m, rpm)
            if ack:
                print(f"✅ Motor {m} RPM set @ {rpm}  ACK={ack.hex()}")
            else:
                print(f"⚠️  Motor {m} no ACK on set RPM")

            # # 2) start
            # ack = self.motor.start(m, forward)
            # if ack:
            #     dir_str = 'forward' if forward else 'reverse'
            #     print(f"✅ Motor {m} started {dir_str}  ACK={ack.hex()}")
            # else:
            #     print(f"⚠️  Motor {m} no ACK on start")


    def balance_loop(self, sample_hz: float = 20.0):
        """
        Example PID loop: maintain roll=0° by adjusting winch RPM.
        """
        period = 1.0 / sample_hz
        self.imu.connect()
        stream = self.imu.stream()
        self.pid.reset()

        last_t = time.time()
        try:
            for reading in stream:
                now = time.time()
                dt  = now - last_t if now > last_t else period
                last_t = now

                # Measure current RPM on motor #1
                meas = self.motor.read_speed(self.motor.addresses[0]) or 0.0

                # Compute PID correction around base_rpm
                corr = self.pid.update(0.0, reading.roll, dt)
                cmd  = self.base_rpm + corr

                # Engage without extra sleep
                self.engage_motors(cmd)
                print(f"roll={reading.roll:+.2f}°  →  rpm={meas:.0f}  cmd={cmd:.0f}")

                # Maintain loop timing
                delay = period - (time.time() - now)
                if delay > 0:
                    time.sleep(delay)

        finally:
            self.stop_all(brake=False)
            self.imu.close()

if __name__ == '__main__':
    bot = ArachnoNestor([1,2,3,4], base_rpm=500)
    bot.engage_motors(500)
