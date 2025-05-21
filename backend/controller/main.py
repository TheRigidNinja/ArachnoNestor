#!/usr/bin/env python3
import argparse, signal, sys, time
from controller import ArachnoNestor

def main():
    p = argparse.ArgumentParser(description="ArachnoNestor runner")
    p.add_argument('--rpm',    type=int,   required=True,
                   help='Target/base RPM')
    p.add_argument('--motors', nargs='+', type=int, required=True,
                   help='List of motor addresses to run (e.g. 1 2 3)')
    p.add_argument('--mode', choices=['manual','pid'], default='manual',
                   help='manual = just spin, pid = closed-loop speed control')
    p.add_argument('--hz',     type=float, default=20.0,
                   help='Loop rate for PID (only in pid mode)')
    p.add_argument('--duration', type=float, default=None,
                   help='Auto-stop after N seconds (optional)')
    p.add_argument('--err-threshold', type=float, default=200,
                   help='Max allowed RPM error before abort (pid mode)')
    p.add_argument('--err-timeout',   type=float, default=2.0,
                   help='Seconds error must persist to kill (pid mode)')
    args = p.parse_args()

    bot = ArachnoNestor(args.motors, base_rpm=args.rpm)

    def kill(msg=''):
        print(f"\n🛑 Aborting: {msg}")
        bot.stop_all(brake=True)
        sys.exit(1)

    # catch Ctrl-C
    signal.signal(signal.SIGINT, lambda *a: kill('SIGINT'))

    # manual mode: fire & hold until Ctrl-C or timeout
    if args.mode == 'manual':
        bot.engage_motors(args.rpm)
        print(f"▶  Manual: motors {args.motors} @ {args.rpm} RPM. Ctrl-C to stop.")
        if args.duration:
            time.sleep(args.duration)
            kill('timeout')
        else:
            signal.pause()

    # pid mode: closed-loop speed control + watchdog
    else:
        print(f"▶  PID mode: target {args.rpm} RPM on {args.motors}. Ctrl-C to abort.")
        bot.imu.connect()
        stream     = bot.imu.stream()
        bot.pid.reset()

        last_time  = time.time()
        err_start  = None
        start_time = last_time
        try:
            for _reading in stream:
                now   = time.time()
                dt    = now - last_time
                last_time = now

                # read actual RPM (first motor as representative)
                meas = bot.motor.read_speed(args.motors[0]) or 0

                # PID correction & fire
                corr = bot.pid.update(args.rpm, meas, dt)
                cmd  = args.rpm + corr
                bot.engage_motors(cmd)
                print(f"rpm={meas:.0f} → cmd={cmd:.0f}   err={abs(args.rpm-meas):.0f}")

                # watchdog: too big an error for too long?
                err = abs(args.rpm - meas)
                if err > args.err_threshold:
                    if err_start is None:
                        err_start = now
                    elif now - err_start > args.err_timeout:
                        kill(f"RPM error {err:.0f} > {args.err_threshold} for {args.err_timeout}s")
                else:
                    err_start = None

                # timeout?
                if args.duration and now - start_time > args.duration:
                    kill("timeout")

                # maintain loop rate
                sleep = (1.0/args.hz) - (time.time() - now)
                if sleep > 0:
                    time.sleep(sleep)

        finally:
            bot.imu.close()
            bot.stop_all(brake=False)

if __name__ == '__main__':
    main()
