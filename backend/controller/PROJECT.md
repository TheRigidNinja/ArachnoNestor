## Overview

This repo controls winch motors over Modbus/RS-485 and reads sensors over an ESP32-EVB TCP link.
The architecture is split into:

- **Motion authority**: `motor/motion_controller.py`
  - Owns mode, fault, safety gating, and all movement commands.
  - Enforces hall safety (no movement if any hall < threshold).
- **TCP comms**: `tcp/evb.py`
  - Single source for EVB sensor requests (bundle, IMU, distance, etc.).
- **Web UI/API**: `app/web_control.py`
  - Sends requests to the motion controller; never drives motors directly.
- **Protocol definitions**: `protocol/`
- **Drivers**: `drivers/` (hardware access only)
- **Config**: `config/config.yaml`
- **Logging**: `logutil/`

## Execution model

Official entry point: `app/main.py`.
Root `main.py` is a compatibility shim.

## Safety

- Any EVB comm failure or hall-threshold violation triggers a stop and FAULT.
- FAULT blocks all motion until cleared.
