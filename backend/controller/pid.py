class PID:
    def __init__(self, kp, ki, kd, mn=0, mx=4000):
        self.kp, self.ki, self.kd = kp, ki, kd
        self.mn, self.mx = mn, mx
        self._int = 0.0
        self._last_err = None

    def reset(self):
        self._int = 0.0
        self._last_err = None

    def update(self, setpoint, measurement, dt):
        err = setpoint - measurement
        self._int += err * dt
        der = 0.0 if (self._last_err is None) else (err - self._last_err)/dt
        self._last_err = err

        # PID formula
        out = self.kp*err + self.ki*self._int + self.kd*der
        # clamp to [mn, mx]
        return max(self.mn, min(self.mx, out))
