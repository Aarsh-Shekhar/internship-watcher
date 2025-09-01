# 05_watch_sources.py
import argparse
from watch_core import run_scan

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument(
        "--seed",
        action="store_true",
        help="Mark everything currently visible as seen; no notifications.",
    )
    args = p.parse_args()
    run_scan(seed=args.seed)
