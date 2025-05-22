#!/usr/bin/env python3
import argparse
import signal
import sys
import time

from controller import ArachnoNestor

def main():
    p = argparse.ArgumentParser(description="ArachnoNestor runner")
    p.add_argument('--rpm',       type=int,   required=True,
                   help='Target/base RPM')
    p.add_argument('--motors',    nargs='+', type=int, required=True,
                   help='Motor IDs to run (e.g. 1 2 3)')
    p.add_argument('--mode',      choices=['manual','pid'], default='manual',
                   help='manual: spin constant; pid: closed-loop control')
    p.add_argument('--hz',        type=float, default=20.0,
                   help='Loop frequency (PID mode only)')
    p.add_argument('--duration',  type=float, default=None,
                   help='Auto-stop after N seconds')
    p.add_argument('--err-threshold', type=float, default=200,
                   help='Max RPM error before abort (PID)')
    p.add_argument('--err-timeout',   type=float, default=2.0,
                   help='Seconds error must persist to kill (PID)')
    args = p.parse_args()

    bot = ArachnoNestor(args.motors, base_rpm=args.rpm)

    def kill(msg=''):
        print(f"\n🛑 Abort: {msg}")

        # bot.stop_all(brake=False)
        # bot.stop_all(brake=False)
        bot.motor.stop_all(brake=False)

        # bot.stop_all(brake=True)
        sys.exit(1)

    signal.signal(signal.SIGINT, lambda *a: kill('SIGINT'))

    # MANUAL mode: fire & hold
    if args.mode == 'manual':
        bot.engage_motors(args.rpm,forward=True)
        print(f"▶ Manual: motors {args.motors} @ {args.rpm} RPM.")
        if args.duration:
            time.sleep(args.duration)
            kill('timeout')
        else:
            signal.pause()

    # PID mode: closed-loop with watchdog
    now0 = time.time()
    err_start = None
    bot.imu.connect()
    stream = bot.imu.stream()
    bot.pid.reset()
    print(f"▶ PID: target {args.rpm} RPM on {args.motors}.")

    try:
        for _ in stream:
            t1 = time.time()
            meas = bot.motor.read_speed(args.motors[0]) or 0.0
            corr = bot.pid.update(args.rpm, meas, 1.0/args.hz)
            cmd  = args.rpm + corr
            bot.engage_motors(cmd)

            err = abs(args.rpm - meas)
            if err > args.err_threshold:
                if err_start is None:
                    err_start = t1
                elif t1 - err_start >= args.err_timeout:
                    kill(f"RPM error {err:.0f} > {args.err_threshold}")
            else:
                err_start = None

            if args.duration and (t1 - now0) >= args.duration:
                kill('timeout')

            print(f"rpm={meas:.0f}  cmd={cmd:.0f}  err={err:.0f}")
            # enforce loop rate
            delta = (1.0/args.hz) - (time.time() - t1)
            if delta > 0:
                time.sleep(delta)

    finally:
        bot.imu.close()
        bot.stop_all(brake=False)

if __name__ == '__main__':
    main()
