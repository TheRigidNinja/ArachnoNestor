# Robot Control (Testing Env)

## What this is
Motion control and sensor monitoring for a winch-based robot. The motion controller enforces safety and mode rules, while the web UI issues requests.

## Key modules

- `motor/motion_controller.py` — **movement authority** (modes, safety gating, motor commands).
- `tcp/evb.py` — **EVB TCP comms** (bundle/IMU/distance; all sensor access goes through here).
- `app/web_control.py` — minimal web UI + API (requests only).
- `app/main.py` — **official entry point** (root `main.py` is a shim).
- `protocol/` — EVB framing + CRC definitions.
- `drivers/` — hardware access wrappers (TCP/UART).
- `config/config.yaml` — IPs, ports, thresholds.
- `logutil/` — centralized logging helpers.

## Run the web UI

```bash
python3 app/main.py
```

Compatibility shim:

```bash
python3 main.py
```

Then open:
- `http://<mini_pc_ip>:8080/`
- `http://<mini_pc_ip>:8080/status`

## Safety rules

- If any hall < 1500, all motors stop and system enters FAULT.
- FAULT blocks all movement until `clear_fault()` is called.
- Mode changes always stop motors first.

## Endpoints (summary)

- `GET /status`
- `POST /mode/idle|setup|test`
- `POST /fault/clear`
- `POST /stop` (soft)
- `POST /stop/all` (emergency fault stop)
- `POST /setup/jog`
- `POST /test/up`
- `POST /test/dir/<forward|back|left|right|up|down>`
