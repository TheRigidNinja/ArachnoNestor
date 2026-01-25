"""Speed profiles and PID tuning constants."""


class PID:
    def __init__(self, kp, ki, kd, mn, mx):
        self.kp, self.ki, self.kd = kp, ki, kd
        self.mn, self.mx = mn, mx
        self.setpoint = 0.0
        self._int = 0.0
        self._last = None

    def update(self, measure, dt):
        err = self.setpoint - measure
        self._int += err * dt
        der = 0 if self._last is None else (err - self._last) / dt
        self._last = err
        u = self.kp * err + self.ki * self._int + self.kd * der
        return max(self.mn, min(self.mx, u))


DEFAULT_BALANCE_PID = {
    "kp": 20.0,
    "ki": 0.1,
    "kd": 5.0,
    "mn": -1000,
    "mx": 1000,
}
