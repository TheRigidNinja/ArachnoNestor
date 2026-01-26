## Project Rules

1) Movement authority lives in `motor/motion_controller.py`.
   - Other modules only request actions; the motion controller decides if it runs.

2) All EVB sensor data access goes through `tcp/evb.py`.
   - No other module can open sockets or parse EVB packets.

3) Hardware access is isolated.
   - Only code in `drivers/` may touch hardware protocols (TCP/UART/GPIO).
   - Everything else consumes clean APIs.

4) Protocol definitions live in one place: `protocol/`.
   - No magic bytes/registers scattered across the codebase.

5) Safety first.
   - If comms drop or sensor data is stale, motion controller enters safe state immediately.

6) Keep control paths deterministic.
   - No long blocking calls inside control loops.
   - Use timeouts everywhere.

7) Configuration is centralized.
   - IPs/ports/IDs live in `config/` (yaml/env), not hardcoded.

8) Logging is structured and consistent.
   - All modules log via one logger, with timestamps + module names.

9) New capabilities should be added as importable modules, then wired in `app/main.py`.

10) Any change that can move hardware must include a safety check + test.

No module may directly control hardware unless it is explicitly designated
as a driver or the motion controller. Violations are bugs, not features.
