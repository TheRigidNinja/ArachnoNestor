#!/usr/bin/env python3
"""Minimal EVB skeleton: ping + snapshot + delta for all winches."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Optional

# Ensure repo root on sys.path when running from app/
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from config.settings import load_config
from drivers.evb_driver import EVBDriver
from logutil.logger import get_logger
from tcp import evb as evb_api

log = get_logger("app.evb_skeleton")


def run(
    host: Optional[str] = None,
    port: Optional[int] = None,
    timeout: Optional[float] = None,
    interval: Optional[float] = None,
    once: bool = False,
) -> int:
    cfg = load_config()
    host = host or cfg["evb"]["host"]
    port = port if port is not None else cfg["evb"]["port"]
    timeout = timeout if timeout is not None else cfg["evb"]["timeout"]
    interval = interval if interval is not None else cfg["motion"]["poll_interval"]
    winch_ids = cfg["motion"]["winch_ids"]

    log.info(
        f"EVB skeleton start host={host} port={port} timeout={timeout} "
        f"interval={interval} winches={winch_ids}"
    )
    try:
        with EVBDriver(host, port, timeout) as evb:
            try:
                evb_api.ping(evb.client)
                log.info("ping ok")
            except Exception as exc:
                log.error(f"ping failed: {exc}")

            last_snapshot = {}
            last_delta = {}
            last_distance = None
            last_imu = None
            last_power = {}
            while True:
                halls = {}
                power = {}
                combined = []
                for w in winch_ids:
                    snap = evb.snapshot(w)
                    r_winch, delta, _cache_age = evb_api.get_delta(evb.client, w)
                    halls[w] = snap.hall_raw
                    prev_snap = last_snapshot.get(w)
                    prev_delta = last_delta.get(w)
                    if prev_snap != snap or prev_delta != delta:
                        log.info(
                            f"winch={w} snap(winch={snap.winch} total={snap.total_count} hall={snap.hall_raw} "
                            f"cache_age_ms={snap.cache_age_ms}) delta(winch={r_winch} delta={delta})"
                        )
                        last_snapshot[w] = snap
                        last_delta[w] = delta
                    try:
                        bundle = evb_api.get_bundle(evb.client, w)
                        power[w] = {
                            "bus_mv": bundle["bus_mv"],
                            "current_ma": bundle["current_ma"],
                            "power_mw": bundle["power_mw"],
                        }
                    except Exception as exc:
                        log.error(f"power read failed winch={w}: {exc}")
                    combined.append({
                        "winch": w,
                        "hall": halls.get(w, 0),
                        "bus_mv": power.get(w, {}).get("bus_mv", 0),
                        "current_ma": power.get(w, {}).get("current_ma", 0),
                        "power_mw": power.get(w, {}).get("power_mw", 0),
                    })

                try:
                    dist = evb_api.get_distance(evb.client)
                except Exception as exc:
                    dist = None
                    log.error(f"distance read failed: {exc}")
                if dist is not None and dist != last_distance:
                    log.info(
                        f"distance(ok={dist['ok']} dist_mm={dist['dist_mm']} strength={dist['strength']} "
                        f"temp_raw={dist['temp_raw']} age_ms={dist['age_ms']} cache_age_ms={dist['cache_age_ms']})"
                    )
                    last_distance = dist
                try:
                    imu = evb_api.get_imu(evb.client)
                except Exception as exc:
                    imu = None
                    log.error(f"imu read failed: {exc}")
                if imu is not None and imu != last_imu:
                    log.info(
                        f"imu(gyro={imu['gyro']} accel={imu['accel']} temp_c={imu['temp_c']} "
                        f"pitch={imu['pitch']} roll={imu['roll']} yaw={imu['yaw']})"
                    )
                    last_imu = imu

                if power and power != last_power:
                    log.info(f"power={power}")
                    last_power = power

                line = (
                    f"data={combined} "
                    f"distance={last_distance} "
                    f"imu={'ok' if last_imu else 'none'}"
                )
                print(f"\r{line:<200}", end="", flush=True)

                if once:
                    print()
                    break
                time.sleep(max(0.0, float(interval)))
    except KeyboardInterrupt:
        log.info("EVB skeleton stopped by user")
    except Exception as exc:
        log.error(f"EVB skeleton error: {exc}")
        return 1
    return 0


def main(argv=None) -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
