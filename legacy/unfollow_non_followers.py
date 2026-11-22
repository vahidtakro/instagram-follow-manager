#!/usr/bin/env python3
"""
Unfollow accounts you follow who do not follow you back.

Input: a plain text file with one username per line.

The script only clicks when the profile button actually says "Following" (or
"Unfollow"). It never touches profiles showing "Follow", "Follow Back", or
"Requested", so it cannot accidentally send a follow request or unfollow
someone you were not following.

Same safety model as cancel_pending_requests.py:
  * dry-run is the default, --apply is required to click
  * small batches, long random delays (Instagram rate-limits unfollows hard)
  * resumable state file
  * stops on login wall / challenge / action block
  * an "unverified" click is never retried automatically

Setup:  python3 -m venv .venv && .venv/bin/pip install selenium
Run:    .venv/bin/python unfollow_non_followers.py --limit 10
        .venv/bin/python unfollow_non_followers.py --apply --limit 10
"""

from __future__ import annotations

import argparse
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Reuse the browser/session plumbing from the working follow-request script.
# That module's entry point is guarded by __main__, so importing is safe.
from cancel_pending_requests import (  # noqa: E402
    DISABLED_RE,
    FOLLOW_BACK_RE,
    FOLLOW_RE,
    FOLLOWING_RE,
    NOT_FOUND_RE,
    REQUESTED_RE,
    TimeoutException,
    WebDriverException,
    WebDriverWait,
    body_text,
    load_state,
    log,
    make_driver,
    page_guard,
    save_state,
    visible_button_labels,
    wait_for_state,
)


HERE = Path(__file__).resolve().parent
DEFAULT_FILE = HERE / "non_followers-usernames.txt"
DEFAULT_STATE = HERE / "unfollow_state.json"

# "Following" is the button on profiles you currently follow. Instagram sometimes
# swaps the visible text to "Unfollow" on hover, so accept both.
JS_CLICK_FOLLOWING = r"""
return (function () {
  function vis(e) {
    var s = getComputedStyle(e);
    return e.getClientRects().length > 0 && s.visibility !== 'hidden' &&
           s.display !== 'none' && parseFloat(s.opacity) > 0;
  }
  function label(e) {
    return ((e.innerText || e.textContent || '') + ' ' +
            (e.getAttribute('aria-label') || '')).trim();
  }
  var els = document.querySelectorAll("button, [role='button']");
  for (var i = 0; i < els.length; i++) {
    var e = els[i];
    if (!vis(e)) continue;
    var l = label(e);
    if (/^\s*(following|unfollow)\s*$/i.test(l)) { e.click(); return l; }
  }
  return null;
})()
"""

# The confirmation must come from a real dialog/modal, never from some other
# "Unfollow" link elsewhere on the page.
JS_CLICK_UNFOLLOW_CONFIRM = r"""
return (function () {
  function vis(e) {
    var s = getComputedStyle(e);
    return e.getClientRects().length > 0 && s.visibility !== 'hidden' &&
           s.display !== 'none' && parseFloat(s.opacity) > 0;
  }
  function label(e) {
    return ((e.innerText || e.textContent || '') + ' ' +
            (e.getAttribute('aria-label') || '')).trim();
  }
  var scopes = Array.from(document.querySelectorAll("[role='dialog'], [class*='Modal']"))
                     .filter(vis);
  for (var s = 0; s < scopes.length; s++) {
    var els = scopes[s].querySelectorAll("button, [role='button']");
    for (var i = 0; i < els.length; i++) {
      var e = els[i];
      if (!vis(e)) continue;
      var l = label(e);
      if (/unfollow/i.test(l)) { e.click(); return l; }
    }
  }
  return null;
})()
"""

UNFOLLOWED_STATES = {"follow", "follow_back", "not_found"}


def load_usernames(path: Path) -> list[str]:
    names = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
    names = [n for n in names if n]
    return list(dict.fromkeys(names))


def classify_profile(driver) -> str:
    """Classify the current profile's follow state."""
    guard = page_guard(driver)
    if guard:
        return "abort:" + guard

    text = body_text(driver)
    if NOT_FOUND_RE.search(text):
        return "not_found"
    if DISABLED_RE.search(text):
        return "unavailable"

    labels = visible_button_labels(driver)
    if not text.strip() and not labels:
        return "blank"
    # Most specific first: "Follow Back" means they follow you, so leave them alone.
    if any(FOLLOW_BACK_RE.search(label) for label in labels):
        return "follow_back"
    if any(REQUESTED_RE.search(label) for label in labels):
        return "requested"
    if any(FOLLOWING_RE.match(label) for label in labels):
        return "following"
    if any(FOLLOW_RE.match(label) for label in labels):
        return "follow"
    return "unknown"


def process_profile(driver, username: str, apply: bool) -> tuple[str, str]:
    """Visit one profile. Returns (status, detail)."""
    try:
        driver.get(f"https://www.instagram.com/{username}/")
    except TimeoutException:
        return "error", "page load timed out"

    state = wait_for_state(driver, classify_profile)
    time.sleep(0.4)
    if state.startswith("abort:"):
        return "abort", state.split("abort:", 1)[1]
    if state != "following":
        return state, ""

    if not apply:
        return "would_unfollow", "dry run"

    # --- The actual action ---
    try:
        clicked = driver.execute_script(JS_CLICK_FOLLOWING)
    except WebDriverException as exc:
        return "error", f"click failed: {type(exc).__name__}"
    if not clicked:
        return "error", "Following button disappeared before click"
    log(f"    clicked: {clicked!r}")

    time.sleep(1.0)

    guard = page_guard(driver)
    if guard:
        return "abort", guard

    try:
        confirm = driver.execute_script(JS_CLICK_UNFOLLOW_CONFIRM)
    except WebDriverException:
        confirm = None
    if confirm:
        log(f"    confirming: {confirm!r}")
        time.sleep(1.2)

    guard = page_guard(driver)
    if guard:
        return "abort", guard

    # Verify. Never retry automatically: a second click could follow them again.
    try:
        WebDriverWait(driver, 8).until(
            lambda d: classify_profile(d) != "following" or page_guard(d) is not None
        )
    except TimeoutException:
        pass

    final = classify_profile(driver)
    if final.startswith("abort:"):
        return "abort", final.split("abort:", 1)[1]
    if final in UNFOLLOWED_STATES:
        return "unfollowed", ""
    if final == "following":
        return "unverified", "button still says Following; check manually, do not click again"
    return "unverified", f"unexpected post-click state: {final}"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--file", type=Path, default=DEFAULT_FILE, help="text file, one username per line")
    p.add_argument("--state", type=Path, default=DEFAULT_STATE, help="progress file")
    p.add_argument("--profile", type=Path, default=Path("/root/.config/chrome-instagram"))
    p.add_argument("--cdp", help="attach to running Chrome, e.g. 127.0.0.1:9222")
    p.add_argument("--apply", action="store_true", help="actually unfollow (default: dry run)")
    p.add_argument("--limit", type=int, default=15, help="max profiles this run (default: 15)")
    p.add_argument("--min-delay", type=float, default=45.0)
    p.add_argument("--max-delay", type=float, default=75.0)
    p.add_argument("--only", action="append", default=[])
    p.add_argument("--reset-state", action="store_true")
    p.add_argument("--retry-errors", action="store_true")
    p.add_argument("--headless", action="store_true")
    p.add_argument("--restart-chrome", action="store_true",
                   help="close an existing Chrome using this profile before starting")
    args = p.parse_args()

    if not args.file.exists():
        raise SystemExit(f"Input file not found: {args.file}")
    if args.min_delay < 0 or args.max_delay < args.min_delay:
        raise SystemExit("Invalid delay range")

    usernames = load_usernames(args.file)
    if args.only:
        wanted = set(args.only)
        usernames = [u for u in usernames if u in wanted]
        missing = wanted - set(usernames)
        if missing:
            log(f"WARNING: not present in file: {', '.join(sorted(missing))}")

    if args.reset_state and args.state.exists():
        args.state.unlink()
        log("State file reset")
    state = load_state(args.state)

    # The export marks deleted accounts with a __deleted__ prefix. No point
    # opening a browser tab for those.
    for name in usernames:
        if name.startswith("__deleted__") and name not in state:
            state[name] = {
                "status": "not_found",
                "detail": "marked deleted in input file",
                "time": datetime.now(timezone.utc).isoformat(),
            }
    save_state(args.state, state)

    def handled(username: str) -> bool:
        if username not in state:
            return False
        status = state[username].get("status")
        if status in {"would_unfollow", "abort"}:
            return False
        if args.retry_errors and status in {"error", "unavailable"}:
            return False
        return True

    todo = [u for u in usernames if not handled(u)]
    log(f"{len(usernames)} usernames in file, {len(usernames) - len(todo)} already handled, {len(todo)} to process")
    todo = todo[: args.limit]
    if not todo:
        log("Nothing to do. Use --reset-state to start over.")
        return 0

    log("DRY RUN - no clicks will be made. Add --apply to unfollow." if not args.apply
        else "APPLY MODE - accounts will be unfollowed.")

    driver = make_driver(args)
    aborted = False
    counts: dict[str, int] = {}

    try:
        driver.get("https://www.instagram.com/")
        time.sleep(2)
        guard = page_guard(driver)
        if guard:
            raise SystemExit(f"Preflight failed: {guard}")

        for i, username in enumerate(todo, 1):
            if aborted:
                break
            log(f"[{i}/{len(todo)}] {username}")
            status, detail = process_profile(driver, username, args.apply)
            counts[status] = counts.get(status, 0) + 1
            state[username] = {
                "status": status,
                "detail": detail,
                "time": datetime.now(timezone.utc).isoformat(),
            }
            save_state(args.state, state)
            log(f"    -> {status}" + (f" ({detail})" if detail else ""))

            if status == "abort":
                aborted = True
                log(f"STOPPING: {detail}")
                break

            if i < len(todo):
                pause = random.uniform(args.min_delay, args.max_delay)
                log(f"    sleeping {pause:.0f}s")
                time.sleep(pause)
    finally:
        try:
            driver.quit()
        except WebDriverException:
            pass

    log("Summary: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    log(f"Progress saved to {args.state}")
    if aborted:
        log("Run stopped early for safety. Re-run later to resume.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrupted. Progress so far was saved.")
        sys.exit(130)
