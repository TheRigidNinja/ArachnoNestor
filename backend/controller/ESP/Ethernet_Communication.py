# # You have to always run this on startup until you figure out how to do it automatically
# sudo ip addr flush dev enp1s0
# sudo ip addr add 192.168.2.100/24 dev enp1s0
# sudo ip link set enp1s0 up

#!/usr/bin/env python3
import socket
import json

ESP_IP   = "192.168.2.123"
ESP_PORT = 5000

def send_motor_batch(sock, batch):
    """
    Send one JSON batch to the ESP32 over an already‑open socket.
    """
    payload = {"motors": batch}
    data = json.dumps(payload) + "\n"
    sock.sendall(data.encode())
    # wait for a short ACK
    resp = sock.recv(256)
    if resp:
        print("↩️  ESP reply:", resp.decode().strip())

def interactive_batch():
    """
    Build a batch list interactively.
    User types lines like: 0 on forward 50
    or   3 off reverse 0
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
            m = int(mid)
            batch.append({
              "id":        m,
              "enable":   (s_on.lower() in ("1","on","true","enable")),
              "direction": dirc.lower(),
              "pwm":      int(spwm)
            })
        except:
            print(" ⚠️  invalid format, try again")
    return batch

def main():
    try:
        with socket.create_connection((ESP_IP, ESP_PORT), timeout=5) as sock:
            print(f"✅ Connected to ESP32 at {ESP_IP}:{ESP_PORT}")
            while True:
                cmd = input("Command ([B]atch / QUIT): ").strip().lower()
                if cmd in ("quit","q"):
                    print("🔌 Disconnecting.")
                    break
                elif cmd in ("b","batch"):
                    batch = interactive_batch()
                    if batch:
                        send_motor_batch(sock, batch)
                else:
                    print("Unknown—type B to send a batch or QUIT to exit.")
    except Exception as e:
        print("❌ Connection failed:", e)

if __name__ == "__main__":
    main()




#0 on forward 5