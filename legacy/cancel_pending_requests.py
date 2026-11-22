#!/usr/bin/env python3
"""
Cancel OUTGOING (sent) Instagram follow requests from a JSON export.

The JSON file is expected to be the Instagram data-export format:

    [ { "timestamp": ..., "label_values": [ {"label": "Username", "value": "name"}, ... ] }, ... ]

Only profiles whose follow button still says "Requested" / "Pending" are touched.
Accounts that already accepted your request ("Following") are left alone.

SAFETY DEFAULTS
---------------
* Dry-run is the default. Nothing is clicked unless you pass --apply.
* Only 20 profiles per run, with 25-45s random pauses (Instagram rate-limits
  follow/unfollow actions aggressively; 196 actions in one sitting is a good
  way to get an action block).
* Progress is saved to a state file, so re-runs resume where you left off.
* The script stops immediately if it sees a login wall, security check, or
  "action blocked" message.

Setup:  python3 -m venv .venv && .venv/bin/pip install selenium
Run:    .venv/bin/python cancel_pending_requests.py            # dry run
        .venv/bin/python cancel_pending_requests.py --apply    # actually cancel
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    from selenium import webdriver
    from selenium.common.exceptions import TimeoutException, WebDriverException
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.support.ui import WebDriverWait
except ImportError:
    sys.exit(
        "Missing dependency. Install it with:\n"
        "  python3 -m venv .venv\n"
        "  .venv/bin/pip install selenium"
    )


HERE = Path(__file__).resolve().parent
DEFAULT_JSON = HERE / "pending_follow_requests.json"
DEFAULT_STATE = HERE / "cancel_state.json"
DEFAULT_PROFILE = Path("/root/.config/chrome-instagram")

# Read labels / click entirely inside the page so React re-renders cannot
# invalidate element references between Selenium commands.
JS_BUTTON_LABELS = """
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
  return Array.from(document.querySelectorAll("button, [role='button']"))
              .filter(vis).map(label);
})()
"""

JS_CLICK_REQUESTED = """
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
    if (/\\b(requested|pending)\\b/i.test(l)) { e.click(); return l; }
  }
  return null;
})()
"""

JS_CLICK_CONFIRM = """
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
  var re = /(cancel\\s+(follow\\s+)?request|unfollow|withdraw|remove\\s+request)/i;
  var scopes = Array.from(document.querySelectorAll("[role='dialog'], [class*='Modal']"))
                     .filter(vis);
  if (!scopes.length) scopes = [document];
  for (var s = 0; s < scopes.length; s++) {
    var els = scopes[s].querySelectorAll("button, [role='button']");
    for (var i = 0; i < els.length; i++) {
      var e = els[i];
      if (!vis(e)) continue;
      var l = label(e);
      if (re.test(l)) { e.click(); return l; }
    }
  }
  return null;
})()
"""

REQUESTED_RE = re.compile(r"\b(requested|pending)\b", re.I)
FOLLOWING_RE = re.compile(r"^\s*(following|following you|unfollow)\s*$", re.I)
FOLLOW_RE = re.compile(r"^\s*follow\s*$", re.I)
FOLLOW_BACK_RE = re.compile(r"follow back", re.I)
BLOCK_RE = re.compile(
    r"(action blocked|try again later|we restrict certain activity|"
    r"temporarily blocked|too many (requests|attempts)|suspicious activity)",
    re.I,
)
CHALLENGE_RE = re.compile(
    r"(confirm it's you|security check|enter the code|verify your account|"
    r"confirm your account|challenge required)",
    re.I,
)
NOT_FOUND_RE = re.compile(
    r"(sorry,? this page isn.?t available|the link you followed may be broken|"
    r"this page isn.?t available|page not found)",
    re.I,
)
DISABLED_RE = re.compile(
    r"(account (is )?(suspended|disabled|unavailable)|this account isn.?t available|"
    r"account isn.?t available|profile not found|user not found|no longer available)",
    re.I,
)


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def load_usernames(path: Path) -> list[str]:
    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, list):
        raise ValueError("JSON root must be a list")

    names: list[str] = []
    for entry in data:
        for lv in entry.get("label_values", []):
            if lv.get("label") == "Username" and lv.get("value"):
                names.append(lv["value"].strip())
    # De-duplicate, preserve order.
    return list(dict.fromkeys(names))


def load_state(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        raise SystemExit(f"Could not read state file {path}: {exc}")


def save_state(path: Path, state: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2, ensure_ascii=False)
    tmp.replace(path)


def body_text(driver) -> str:
    try:
        return driver.execute_script(
            "return document.body ? (document.body.innerText || '') : ''"
        ) or ""
    except WebDriverException:
        return ""


def page_guard(driver) -> str | None:
    """Return a reason to abort, or None if the page looks normal."""
    url = driver.current_url or ""
    text = body_text(driver)
    if "/accounts/login/" in url or "/accounts/emailsignup/" in url:
        return "not logged in (redirected to login page)"
    if CHALLENGE_RE.search(text):
        return "Instagram security check / challenge appeared"
    if BLOCK_RE.search(text):
        return "Instagram action block or rate-limit message appeared"
    return None


def visible_button_labels(driver) -> list[str]:
    try:
        return driver.execute_script(JS_BUTTON_LABELS) or []
    except WebDriverException:
        return []


def wait_for_state(driver, classifier, timeout: float = 12.0, blank_after: float = 3.5) -> str:
    """Poll until the page has a usable state.

    Disabled/deactivated accounts often render a completely blank page. Treat
    a page that stays blank for `blank_after` seconds as `unavailable` instead
    of waiting the full timeout and calling it `unknown`.
    """
    deadline = time.time() + timeout
    blank_since: float | None = None
    while True:
        state = classifier(driver)
        if state.startswith("abort:") or state not in {"unknown", "blank"}:
            return state
        if state == "blank":
            if blank_since is None:
                blank_since = time.time()
            elif time.time() - blank_since >= blank_after:
                return "unavailable"
        else:
            blank_since = None
        if time.time() >= deadline:
            return "unavailable" if blank_since is not None else "unknown"
        time.sleep(0.5)


def classify_profile(driver) -> str:
    """Classify the current profile page's follow state.

    All visible buttons are collected first and matched by priority, so a
    coincidental "Follow" button in a sidebar cannot mask a "Requested" button
    on the profile itself.
    """
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

    if any(FOLLOW_BACK_RE.search(label) for label in labels):
        return "follow_back"
    if any(REQUESTED_RE.search(label) for label in labels):
        return "requested"
    if any(FOLLOWING_RE.match(label) for label in labels):
        return "following"
    if any(FOLLOW_RE.match(label) for label in labels):
        return "follow"

    if "/accounts/login/" in (driver.current_url or ""):
        return "abort:not logged in"
    return "unknown"


def click_confirm_button(driver) -> str | None:
    """Click the destructive confirmation in a modal, if one appeared."""
    try:
        return driver.execute_script(JS_CLICK_CONFIRM)
    except WebDriverException:
        return None


def pick_working_display() -> str | None:
    """Find an X display this process can actually connect to."""
    import ctypes
    import ctypes.util

    sockets_dir = Path("/tmp/.X11-unix")
    if not sockets_dir.exists():
        return None
    lib = ctypes.util.find_library("X11")
    if not lib:
        return None
    try:
        x = ctypes.CDLL(lib)
        x.XOpenDisplay.restype = ctypes.c_void_p
        x.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x.XCloseDisplay.argtypes = [ctypes.c_void_p]
        devnull = os.open(os.devnull, os.O_WRONLY)
        saved = os.dup(2)
        for sock in sorted(sockets_dir.glob("X*")):
            display = ":" + sock.name[1:]
            try:
                os.dup2(devnull, 2)  # silence Xauth warnings from failed probes
                handle = x.XOpenDisplay(display.encode())
            finally:
                os.dup2(saved, 2)
            if handle:
                x.XCloseDisplay(handle)
                os.close(devnull)
                os.close(saved)
                return display
        os.close(devnull)
        os.close(saved)
    except Exception:
        pass
    return None


def find_chrome_pids(profile: Path) -> list[int]:
    """Find running Chrome processes using this exact user-data-dir (Linux)."""
    pids: list[int] = []
    proc = Path("/proc")
    if not proc.exists():
        return pids
    needle = f"--user-data-dir={profile}".encode()
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cmdline = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        if needle in cmdline:
            pids.append(int(entry.name))
    return pids


def chrome_lock_status(profile: Path) -> tuple[bool, list[int]]:
    """Return (in_use, pids). Cleans up stale lock symlinks."""
    lock = profile / "SingletonLock"
    pids: list[int] = []

    if lock.is_symlink() or lock.exists():
        try:
            target = os.readlink(lock) if lock.is_symlink() else ""
        except OSError:
            target = ""
        match = re.search(r"-(\d+)$", target)
        if match and Path(f"/proc/{match.group(1)}").exists():
            pids.append(int(match.group(1)))
        elif not target:
            pids.append(-1)  # real file, assume in use
        else:
            try:
                lock.unlink()  # dangling symlink from a dead Chrome
                log("Removed stale SingletonLock")
            except OSError:
                pass

    pids = sorted(set(pids + find_chrome_pids(profile)))
    return bool(pids), [pid for pid in pids if pid > 0]


def terminate_chrome(pids: list[int]) -> None:
    alive = [pid for pid in pids if Path(f"/proc/{pid}").exists()]
    if not alive:
        return
    log(f"Closing existing Chrome (PID {', '.join(map(str, alive))})")
    for pid in alive:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    for _ in range(40):
        if not any(Path(f"/proc/{pid}").exists() for pid in alive):
            break
        time.sleep(0.25)


def make_driver(args) -> webdriver.Chrome:
    opts = Options()
    if args.cdp:
        host_port = args.cdp.replace("http://", "").replace("https://", "").rstrip("/")
        opts.add_experimental_option("debuggerAddress", host_port)
        log(f"Attaching to existing Chrome at {host_port}")
    else:
        profile = args.profile.expanduser().resolve()
        if not profile.exists():
            raise SystemExit(f"Chrome profile not found: {profile}")
        in_use, pids = chrome_lock_status(profile)
        if in_use:
            if args.restart_chrome:
                terminate_chrome(pids)
                in_use, _ = chrome_lock_status(profile)
            if in_use:
                raise SystemExit(
                    f"Chrome is still using this profile ({profile}).\n"
                    "Quit Chrome completely and run this script again, or relaunch\n"
                    "Chrome with --remote-debugging-port=9222 and use --cdp 127.0.0.1:9222,\n"
                    "or pass --restart-chrome to have this script close it for you."
                )
        opts.add_argument(f"--user-data-dir={profile}")
        opts.add_argument("--disable-blink-features=AutomationControlled")
        opts.add_argument("--no-first-run")
        opts.add_argument("--no-default-browser-check")
        opts.add_argument("--start-maximized")
        opts.add_argument("--lang=en-US")
        if os.name == "posix" and hasattr(os, "geteuid") and os.geteuid() == 0:
            opts.add_argument("--no-sandbox")
        if not os.environ.get("DISPLAY"):
            display = pick_working_display()
            if display:
                os.environ["DISPLAY"] = display
                log(f"DISPLAY was unset; using {display}")
            elif not args.headless:
                raise SystemExit(
                    "No usable X display found. Run with --headless, or set DISPLAY\n"
                    "to a display you can connect to."
                )
        if args.headless:
            opts.add_argument("--headless=new")
        log(f"Launching Chrome with profile {profile}")

    opts.set_capability("pageLoadStrategy", "normal")
    driver = webdriver.Chrome(options=opts)
    driver.set_page_load_timeout(30)
    return driver


def process_profile(driver, username: str, apply: bool) -> tuple[str, str]:
    """Visit one profile. Returns (status, detail)."""
    url = f"https://www.instagram.com/{username}/"
    try:
        driver.get(url)
    except TimeoutException:
        return "error", "page load timed out"

    # Give the React app a moment to render the follow button; blank/disabled
    # profiles resolve to "unavailable" quickly instead of burning 12 seconds.
    state = wait_for_state(driver, classify_profile)
    time.sleep(random.uniform(0.4, 0.9))
    if state.startswith("abort:"):
        return "abort", state.split("abort:", 1)[1]
    if state != "requested":
        return state, ""

    if not apply:
        return "would_cancel", "dry run"

    # --- The actual action ---
    try:
        clicked = driver.execute_script(JS_CLICK_REQUESTED)
    except WebDriverException as exc:
        return "error", f"click failed: {type(exc).__name__}"
    if not clicked:
        return "error", "Requested button disappeared before click"
    log(f"    clicked: {clicked!r}")

    time.sleep(random.uniform(0.9, 1.8))

    guard = page_guard(driver)
    if guard:
        return "abort", guard

    confirm_label = click_confirm_button(driver)
    if confirm_label:
        log(f"    confirming: {confirm_label!r}")
        time.sleep(random.uniform(1.0, 2.0))

    guard = page_guard(driver)
    if guard:
        return "abort", guard

    # Verify. IMPORTANT: never retry automatically. If the button still says
    # "Requested" we do NOT click again -- a second click can send a NEW request.
    try:
        WebDriverWait(driver, 8).until(
            lambda d: classify_profile(d) != "requested" or page_guard(d) is not None
        )
    except TimeoutException:
        pass

    final = classify_profile(driver)
    if final.startswith("abort:"):
        return "abort", final.split("abort:", 1)[1]
    if final in {"follow", "follow_back", "not_found"}:
        return "cancelled", ""
    if final == "requested":
        return "unverified", "button still says Requested; do not click again without checking"
    return "unverified", f"unexpected post-click state: {final}"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--json", type=Path, default=DEFAULT_JSON, help="input JSON export")
    p.add_argument("--state", type=Path, default=DEFAULT_STATE, help="progress file (resume support)")
    p.add_argument("--profile", type=Path, default=DEFAULT_PROFILE, help="Chrome user-data-dir")
    p.add_argument("--cdp", help="attach to a running Chrome, e.g. 127.0.0.1:9222")
    p.add_argument("--apply", action="store_true", help="actually click Cancel (default: dry run)")
    p.add_argument("--limit", type=int, default=20, help="max profiles this run (default: 20)")
    p.add_argument("--min-delay", type=float, default=25.0, help="min pause between profiles (seconds)")
    p.add_argument("--max-delay", type=float, default=45.0, help="max pause between profiles (seconds)")
    p.add_argument("--only", action="append", default=[], help="process only this username (repeatable)")
    p.add_argument("--reset-state", action="store_true", help="forget previous progress")
    p.add_argument("--retry-errors", action="store_true", help="re-process usernames whose previous status was 'error'")
    p.add_argument("--headless", action="store_true", help="run Chrome headless (less reliable with Instagram)")
    p.add_argument("--restart-chrome", action="store_true",
                   help="close an existing Chrome using this profile before starting (may lose open tabs)")
    args = p.parse_args()

    if not args.json.exists():
        raise SystemExit(f"Input file not found: {args.json}")
    if args.min_delay < 0 or args.max_delay < args.min_delay:
        raise SystemExit("Invalid delay range")

    usernames = load_usernames(args.json)
    if args.only:
        wanted = set(args.only)
        usernames = [u for u in usernames if u in wanted]
        missing = wanted - set(usernames)
        if missing:
            log(f"WARNING: not present in JSON: {', '.join(sorted(missing))}")

    if args.reset_state and args.state.exists():
        args.state.unlink()
        log("State file reset")
    state = load_state(args.state)

    def handled(u: str) -> bool:
        if u not in state:
            return False
        status = state[u].get("status")
        # Dry runs are informational only, and an aborted run must be retried.
        if status in {"would_cancel", "abort"}:
            return False
        if args.retry_errors and status in {"error", "unavailable"}:
            return False
        return True

    todo = [u for u in usernames if not handled(u)]
    log(f"{len(usernames)} usernames in JSON, {len(usernames) - len(todo)} already handled, {len(todo)} to process")
    todo = todo[: args.limit]
    if not todo:
        log("Nothing to do. Use --reset-state to start over.")
        return 0

    log("DRY RUN - no clicks will be made. Add --apply to actually cancel." if not args.apply
        else "APPLY MODE - pending requests will be cancelled.")

    driver = make_driver(args)
    aborted = False
    counts: dict[str, int] = {}

    try:
        # Sanity-check the session before touching any profile.
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
