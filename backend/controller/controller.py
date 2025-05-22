import time
from motor_controller import MotorController
from imu_client import IMUClient
from pid import PID

class ArachnoNestor:
    """
    High-level API for a 4-motor cable-driven platform (AgroCableBot).
    Motors are ordered: [front_left, front_right, back_right, back_left]
    """
    def __init__(
        self,
        motor_addrs: list[int],
        base_rpm: int = 300,
        pid_params: tuple[float, float, float] = (1.2, 0.01, 0.1),
        pid_limits: tuple[float, float] = (-500, 500)
    ):
        self.motor    = MotorController(addresses=motor_addrs)
        self.imu      = IMUClient()
        self.base_rpm = base_rpm

        kp, ki, kd = pid_params
        mn, mx     = pid_limits
        self.pid    = PID(kp, ki, kd, mn=mn, mx=mx)

    def engage_selected(self, rpm: float, forward: bool, motors: list[int]):
        """
        Set RPM and start for a subset of motors.
        """
        for m in motors:
            ack1 = self.motor.write_rpm(m, rpm)
            if not ack1:
                print(f"⚠️ Motor {m} no ACK on set RPM")
            ack2 = self.motor.start(m, forward)
            if not ack2:
                print(f"⚠️ Motor {m} no ACK on start")

    def engage_motors(self, rpm: float, forward: bool = True):
        """
        Set RPM and start for all motors uniformly.
        """
        self.engage_selected(rpm, forward, self.motor.addresses)

    def move_z(self, rpm: float, up: bool = True):
        """
        Vertical motion: all cables in for up, out for down.
        """
        self.engage_motors(rpm, forward=up)

    def move_x(self, rpm: float, positive: bool = True):
        """
        X-axis motion: positive -> right, negative -> left.
        Right motion reels left cables in and lets right cables out.
        """
        fl, fr, br, bl = self.motor.addresses
        left  = [fl, bl]
        right = [fr, br]
        if positive:
            self.engage_selected(rpm, True,  left)
            self.engage_selected(rpm, False, right)
        else:
            self.engage_selected(rpm, True,  right)
            self.engage_selected(rpm, False, left)

    def move_y(self, rpm: float, positive: bool = True):
        """
        Y-axis motion: positive -> forward, negative -> backward.
        Forward motion reels back cables in and lets front cables out.
        """
        fl, fr, br, bl = self.motor.addresses
        front = [fl, fr]
        back  = [bl, br]
        if positive:
            self.engage_selected(rpm, True,  back)
            self.engage_selected(rpm, False, front)
        else:
            self.engage_selected(rpm, True,  front)
            self.engage_selected(rpm, False, back)

    def stop_all(self, brake: bool = False):
        """
        Stop every motor.
        """
        self.motor.stop_all(brake)

    def balance_loop(self, sample_hz: float = 20.0):
        """
        Simple PID loop to stabilize roll by adjusting vertical.
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

                meas = self.motor.read_speed(self.motor.addresses[0]) or 0.0
                corr = self.pid.update(self.base_rpm, meas, dt)
                cmd  = self.base_rpm + corr

                # adjust vertical for balance
                self.move_z(cmd, up=True)

                elapsed = time.time() - now
                if elapsed < period:
                    time.sleep(period - elapsed)
        finally:
            self.stop_all(brake=False)
            self.imu.close()

    def stabilize(
        self,
        duration: float,
        sample_hz: float = 20.0,
        pid_params: tuple[float, float, float] = (2.0, 0.1, 0.5)
    ):
        """
        Hold z, roll, and pitch at zero using three PID loops.
        duration : how long to stabilize (s)
        sample_hz: update frequency
        pid_params: (kp, ki, kd) for roll/pitch
        """
        # altitude PID (optional, here we only balance orientation)
        # orientation PIDs
        kp, ki, kd = pid_params
        pid_roll  = PID(kp, ki, kd, mn=-self.base_rpm, mx=self.base_rpm)
        pid_pitch = PID(kp, ki, kd, mn=-self.base_rpm, mx=self.base_rpm)

        period = 1.0 / sample_hz
        self.imu.connect()
        stream = self.imu.stream()
        start = time.time()

        try:
            while time.time() - start < duration:
                loop_start = time.time()
                reading = next(stream)
                # get orientation angles
                roll  = reading.roll   # degrees
                pitch = reading.pitch  # degrees

                # compute corrections (desired=0)
                corr_roll  = pid_roll.update(0.0, roll,  period)
                corr_pitch = pid_pitch.update(0.0, pitch, period)

                # base RPM plus corrections
                base = self.base_rpm
                fl, fr, br, bl = self.motor.addresses
                # calculate each motor's RPM
                rpms = {
                    fl: base - corr_roll + corr_pitch,
                    fr: base + corr_roll + corr_pitch,
                    br: base + corr_roll - corr_pitch,
                    bl: base - corr_roll - corr_pitch,
                }
                # send commands
                for m, rpm in rpms.items():
                    self.motor.write_rpm(m, rpm)
                    self.motor.start(m, True)

                # maintain loop rate
                elapsed = time.time() - loop_start
                delay = period - elapsed
                if delay > 0:
                    time.sleep(delay)
        finally:
            self.stop_all()
            self.imu.close()
            self.stop_all()
            self.imu.close()

if __name__ == '__main__':
    bot = ArachnoNestor([1,2,3,4], base_rpm=500)

    # Example: Move up for 3 seconds then stop
    bot.move_z(400, up=True)
    time.sleep(3)
    bot.stop_all()

    # Example: Move +X for 2 seconds then stop
    bot.move_x(300, positive=True)
    time.sleep(2)
    bot.stop_all()

    # Example: Move -Y for 2 seconds then stop
    bot.move_y(300, positive=False)
    time.sleep(2)
    bot.stop_all()
