#!/usr/bin/env python3
import socket
import json
import time

ESP_IP   = "192.168.2.123"
ESP_PORT = 5000

# -- Low-level socket wrappers ------------------------------------------
def send_raw(sock, s: str):
    sock.sendall(s.encode())

def recv_line(sock, timeout: float = 2.0) -> str:
    sock.settimeout(timeout)
    data = b""
    while True:
        chunk = sock.recv(1)
        if not chunk or chunk == b"\n":
            break
        data += chunk
    return data.decode().strip()

# -- JSON batch sender --------------------------------------------------
def send_motor_batch(sock, batch: list):
    """
    Send one JSON batch to the ESP32 over an already-open socket.
    Batch = list of { "id":int, "enable":bool, "direction":"forward"/"reverse", "pwm":int }
    """
    payload = {"motors": batch}
    s = json.dumps(payload) + "\n"
    send_raw(sock, s)
    # wait for simple ack:
    ack = recv_line(sock)
    print("↩️  ESP reply:", ack)

# -- Single-motor raw commands ------------------------------------------
def get_pulses(sock, m: int) -> int:
    """Ask ESP32 for motor m's pulse count."""
    cmd = f"GET_PULSES_{m}\n"
    send_raw(sock, cmd)
    resp = recv_line(sock)
    # expect "M{m} Pulses={value}"
    try:
        _, val = resp.split("=", 1)
        return int(val)
    except:
        raise RuntimeError("Bad GET_PULSES response: " + resp)

def set_pwm(sock, m: int, pct: int):
    send_raw(sock, f"SET_PWM_{m}_{pct}\n")

def set_enable(sock, m: int, on: bool):
    cmd = "ENABLE"  if on else "DISABLE"
    send_raw(sock, f"{cmd}_{m}\n")

def set_direction(sock, m: int, forward: bool):
    cmd = "FORWARD" if forward else "REVERSE"
    send_raw(sock, f"{cmd}_{m}\n")

# -- High-level motion helper --------------------------------------------
def move_distance(sock, motor: int,
                  forward: bool,
                  pwm_pct: int,
                  distance_m: float,
                  pulses_per_rev: int,
                  wheel_circumference_m: float):
    """
    Spin motor in 'forward' (True) or reverse (False) at pwm_pct until
    it moves 'distance_m' meters (using pulses_per_rev & wheel_circumference_m).
    """
    # 1) sample start
    start = get_pulses(sock, motor)
    print(f"↪️  start pulses = {start}")

    # 2) configure motor
    set_enable(sock, motor, True)
    set_direction(sock, motor, forward)
    set_pwm(sock, motor, pwm_pct)

    # 3) compute how many pulses we need
    target_pulses = distance_m / wheel_circumference_m * pulses_per_rev
    print(f"🎯 target delta pulses = {target_pulses:.1f}")

    # 4) wait until reached
    while True:
        now = get_pulses(sock, motor)
        delta = now - start
        print(f"  ↪ now={now}  delta={delta}", end="\r")
        if forward and delta >= target_pulses:
            break
        if not forward and delta <= -target_pulses:
            break
        time.sleep(0.02)

    # 5) stop
    set_pwm(sock, motor, 0)
    set_enable(sock, motor, False)
    print(f"\n✅ reached distance, final pulses={get_pulses(sock, motor)}")

# -- Interactive CLI -----------------------------------------------------
def interactive_batch():
    """
    Build a batch list interactively.
    User types lines like: 0 on forward 50
      (id, enable:on|off, forward|reverse, pwm%)
    Terminate by empty line.
    """
    batch = []
    print("Enter one line per motor: '<id> <on/off> <forward/reverse> <pwm%>'")
    print("Blank line when done.")
    while True:
        line = input("→ ").strip()
        if not line:
            break
        try:
            mid, s_on, dirc, spwm = line.split()
            m  = int(mid)
            on = s_on.lower() in ("1","on","true","enable")
            pw = int(spwm)
            batch.append({
                "id":        m,
                "enable":    on,
                "direction": dirc.lower(),
                "pwm":       pw
            })
        except:
            print(" ⚠️  invalid format, try again")
    return batch

def main():
    print(f"Connecting to ESP32 at {ESP_IP}:{ESP_PORT}…")
    try:
        socket.create_connection((ESP_IP, ESP_PORT), timeout=5)
        # with socket.create_connection((ESP_IP, ESP_PORT), timeout=5) as sock:
        #     print("✅ Connected.  Commands:")
        #     print("  BATCH     → interactive JSON batch send")
        #     print("  MOVE      → move one motor by distance")
        #     print("  QUIT      → exit")
        #     while True:
        #         cmd = input("> ").strip().lower()
        #         if cmd in ("quit","q"):
        #             break
        #         elif cmd in ("batch","b"):
        #             batch = interactive_batch()
        #             if batch:
        #                 send_motor_batch(sock, batch)
        #         elif cmd in ("move","m"):
        #             motor = int(input(" Motor ID: "))
        #             dirc  = input(" Forward? [y/N]: ").strip().lower().startswith("y")
        #             pwm   = int(input(" PWM % (0–100): "))
        #             dist  = float(input(" Distance (m): "))
        #             ppr   = int(input(" Pulses/rev: "))
        #             circ  = float(input(" Wheel circ (m): "))
        #             move_distance(sock, motor, dirc, pwm, dist, ppr, circ)
        #         else:
        #             print("Unknown—type BATCH, MOVE, or QUIT.")
    except Exception as e:
        print("❌ Connection failed:", e)

if __name__ == "__main__":
    main()


# 0 on forward 10
# 0 off reverse 10
# 0 off forward 10