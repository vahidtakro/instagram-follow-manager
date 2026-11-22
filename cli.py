#!/usr/bin/env python3
"""Command-line interface (useful on headless servers)."""

from __future__ import annotations

import argparse
import queue
import sys
import time
from pathlib import Path

from insta_manager.engine import DEFAULT_STATE, Engine, Options
from insta_manager.inputs import load_usernames
from insta_manager.state import StateStore

STATE_DIR = Path.home() / ".instagram-follow-manager"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Cancel requests, unfollow non-followers, like feed posts/reels, or watch stories."
    )
    p.add_argument("mode", choices=["cancel", "unfollow", "engage", "stories"])
    p.add_argument("file", nargs="?", help=".txt, .json or .csv file containing usernames (optional for social modes)")
    p.add_argument("--apply", action="store_true", help="actually click (default: dry run)")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--min-delay", type=float, default=25.0)
    p.add_argument("--max-delay", type=float, default=45.0)
    p.add_argument("--profile", default=str(Path.home() / ".config" / "chrome-instagram"))
    p.add_argument("--cdp", default="")
    p.add_argument("--headless", action="store_true")
    p.add_argument("--restart-chrome", action="store_true")
    p.add_argument("--retry-errors", action="store_true")
    p.add_argument("--driver-path", default="", help="explicit ChromeDriver path (auto-detected if blank)")
    p.add_argument("--fixed-delay", action="store_true",
                   help="use the 'from' delay exactly instead of a random jitter")
    p.add_argument("--reset-state", action="store_true")
    p.add_argument("--state", help="override state file path")
    # Human-paced social options
    p.add_argument("--like-chance", type=int, default=35, help="percent of feed/reel posts to like")
    p.add_argument("--dwell-min", type=float, default=4.0, help="minimum seconds looking at a post")
    p.add_argument("--dwell-max", type=float, default=12.0, help="maximum seconds looking at a post")
    p.add_argument("--skip-chance", type=int, default=30, help="percent of stories to skip early")
    p.add_argument("--story-min", type=float, default=3.0)
    p.add_argument("--story-max", type=float, default=9.0)
    p.add_argument("--no-reels", action="store_true", help="do not browse reels in engage mode")
    p.add_argument("--watch-stories", action="store_true", help="also watch stories before engaging")
    return p


def main() -> int:
    args = build_parser().parse_args()
    usernames: list[str] = []
    if args.file:
        try:
            usernames, warnings = load_usernames(args.file)
        except Exception as exc:  # noqa: BLE001
            print(f"Could not load input: {exc}", file=sys.stderr)
            return 2
        for warning in warnings:
            print(f"warning: {warning}")
    elif args.mode not in {"engage", "stories"}:
        print("A username file is required for this mode.", file=sys.stderr)
        return 2

    state_path = Path(args.state) if args.state else STATE_DIR / DEFAULT_STATE.get(
        args.mode, f"{args.mode}_state.json"
    )
    state_path.parent.mkdir(parents=True, exist_ok=True)
    if args.reset_state:
        StateStore(state_path).reset()

    options = Options(
        mode=args.mode,
        apply=args.apply,
        limit=args.limit,
        min_delay=args.min_delay,
        max_delay=args.max_delay,
        profile=args.profile,
        cdp=args.cdp,
        headless=args.headless,
        restart_chrome=args.restart_chrome,
        retry_errors=args.retry_errors,
        driver_path=args.driver_path,
        random_delay=not args.fixed_delay,
        like_chance=args.like_chance,
        dwell_min=args.dwell_min,
        dwell_max=args.dwell_max,
        skip_chance=args.skip_chance,
        story_min=args.story_min,
        story_max=args.story_max,
        watch_reels=not args.no_reels,
        watch_stories=args.watch_stories,
        follow_only=bool(usernames),
    )
    engine = Engine(options, StateStore(state_path), usernames)
    engine.start()

    try:
        while engine.running or not engine.events.empty():
            try:
                kind, values = engine.events.get(timeout=0.2)
            except queue.Empty:
                continue
            if kind == "log":
                print(values[0], flush=True)
            elif kind == "done":
                counts, aborted, done_count, total, *rest = values
                print("Summary: " + (", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "nothing"))
    except KeyboardInterrupt:
        print("\nStopping...")
        engine.stop()
    engine.join(5)
    return 0


if __name__ == "__main__":
    sys.exit(main())
