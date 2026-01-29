import argparse
import time

import serial

import BLD510BController as bld



# python3 SimpleMotorSequenceTest.py --address 1
# python3 SimpleMotorSequenceTest.py --address 2 --rpm 300
# python3 SimpleMotorSequenceTest.py --address 3 --seconds 5
# If “OUT” is the other direction on your wiring: 
# 
# 
# python3 SimpleMotorSequenceTest.py --address 4 --out-dir R
# It runs: OUT 5s → IN 5s 


def _open_serial(port: str, baud: int, timeout: float) -> serial.Serial:
    return serial.Serial(
        port=port,
        baudrate=baud,
        parity=bld.PARITY,
        stopbits=bld.STOP_BITS,
        bytesize=bld.BYTE_SIZE,
        timeout=timeout,
    )


def _segment(ser: serial.Serial, *, direction: str, rpm: int, seconds: float) -> None:
    bld.write_rpm(ser, rpm)
    bld.start_motorFR(ser, direction)
    time.sleep(seconds)
    bld.stop_motor_natural(ser)
    time.sleep(0.2)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Simple motor sequence test: OUT 5s, IN 5s, OUT 5s.",
    )
    parser.add_argument("--port", default=bld.SERIAL_PORT, help="Serial port (e.g. /dev/ttyUSB0 or /dev/serial/by-id/...)")
    parser.add_argument("--baud", type=int, default=bld.BAUD_RATE)
    parser.add_argument("--timeout", type=float, default=bld.TIMEOUT)
    parser.add_argument("--address", type=int, required=True, help="Modbus motor address (slave/unit id)")
    parser.add_argument("--rpm", type=int, default=300, help="RPM to command during each segment")
    parser.add_argument("--seconds", type=float, default=5.0, help="Seconds per segment (default 5)")
    parser.add_argument("--out-dir", choices=["F", "R"], default="F", help="Which direction means OUT (default F)")
    args = parser.parse_args()

    out_dir = args.out_dir
    in_dir = "R" if out_dir == "F" else "F"

    # Set default address used by bld.* helpers (they call send_modbus_command without device_address=)
    bld.DEVICE_ADDRESS = int(args.address)

    print(f"Port: {args.port} baud={args.baud} timeout={args.timeout}")
    print(f"Motor address: {bld.DEVICE_ADDRESS}")
    print(f"Sequence: OUT({out_dir}) {args.seconds}s, IN({in_dir}) {args.seconds}s, OUT({out_dir}) {args.seconds}s @ {args.rpm} RPM")

    ser = _open_serial(args.port, args.baud, args.timeout)
    try:
        _segment(ser, direction=out_dir, rpm=args.rpm, seconds=args.seconds)
        _segment(ser, direction=in_dir, rpm=args.rpm, seconds=args.seconds)
        _segment(ser, direction=out_dir, rpm=args.rpm, seconds=args.seconds)
    except KeyboardInterrupt:
        print("\nStopping motor...")
        try:
            bld.stop_motor_natural(ser)
        except Exception:
            pass
        return 130
    finally:
        try:
            ser.close()
        except Exception:
            pass

    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

