#!/usr/bin/env python3
"""Single entry point for EVB tools."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="EVB tools")
    ap.add_argument("--web", action="store_true", help="run web dashboard")
    ap.add_argument("--skeleton", action="store_true", help="run EVB skeleton")
    args = ap.parse_args(argv)

    if not (args.web or args.skeleton):
        args.web = True
        args.skeleton = True

    if args.web and args.skeleton:
        from threading import Thread
        from app.evb_skeleton import run as skel_run
        from app.web_dashboard import main as web_main
        skel_thread = Thread(target=skel_run, daemon=True)
        skel_thread.start()
        return web_main() or 0

    if args.web:
        from app.web_dashboard import main as web_main
        return web_main() or 0

    from app.evb_skeleton import main as skel_main
    return skel_main() or 0


if __name__ == "__main__":
    raise SystemExit(main())
