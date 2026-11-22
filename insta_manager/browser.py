"""Browser automation helpers shared by the GUI and CLI.

All DOM reads/clicks are performed with execute_script so Instagram's React
re-renders cannot invalidate element references between Selenium commands.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import re
import signal
import time
from pathlib import Path

from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait

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

JS_BUTTON_LABELS = r"""
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

JS_CLICK_REQUESTED = r"""
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
    if (/\b(requested|pending)\b/i.test(label(e))) { e.click(); return label(e); }
  }
  return null;
})()
"""

JS_CLICK_CANCEL_CONFIRM = r"""
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
  var re = /(cancel\s+(follow\s+)?request|unfollow|withdraw|remove\s+request)/i;
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
    if (/^\s*(following|unfollow)\s*$/i.test(label(e))) { e.click(); return label(e); }
  }
  return null;
})()
"""

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


def pick_working_display() -> str | None:
    """Find an X display this process can actually connect to."""
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
                os.dup2(devnull, 2)
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


def pid_alive(pid: int) -> bool:
    """A zombie process still has /proc/<pid> but is not running."""
    stat = Path(f"/proc/{pid}/stat")
    if not stat.exists():
        return False
    try:
        state = stat.read_text().split(") ", 1)[1][0]
        return state != "Z"
    except (OSError, IndexError):
        return False


def chrome_lock_status(profile: Path) -> tuple[bool, list[int]]:
    """Return (in_use, pids). Removes stale lock symlinks from dead Chrome runs."""
    lock = profile / "SingletonLock"
    pids: list[int] = []
    if lock.is_symlink() or lock.exists():
        try:
            target = os.readlink(lock) if lock.is_symlink() else ""
        except OSError:
            target = ""
        match = re.search(r"-(\d+)$", target)
        if match and pid_alive(int(match.group(1))):
            pids.append(int(match.group(1)))
        elif not target:
            pids.append(-1)
        else:
            try:
                lock.unlink()
            except OSError:
                pass
    pids = sorted(set(pids + find_chrome_pids(profile)))
    return bool(pids), [pid for pid in pids if pid > 0]


def terminate_chrome(pids: list[int], emit=None) -> None:
    alive = [pid for pid in pids if pid_alive(pid)]
    if not alive:
        return
    if emit:
        emit("log", f"Closing existing Chrome ({', '.join(map(str, alive))})")
    for pid in alive:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    for _ in range(40):
        if not any(pid_alive(pid) for pid in alive):
            break
        time.sleep(0.25)


def make_driver(profile: str | Path, cdp: str | None = None, headless: bool = False,
                restart_chrome: bool = False, emit=None):
    """Start (or attach to) Chrome and return a WebDriver instance."""
    from selenium import webdriver  # imported lazily so GUI can start even if missing

    opts = Options()
    if cdp:
        host_port = cdp.replace("http://", "").replace("https://", "").rstrip("/")
        opts.add_experimental_option("debuggerAddress", host_port)
        if emit:
            emit("log", f"Attaching to Chrome at {host_port}")
    else:
        path = Path(profile).expanduser().resolve()
        if not path.exists():
            raise RuntimeError(f"Chrome profile not found: {path}")
        in_use, pids = chrome_lock_status(path)
        if in_use:
            if restart_chrome:
                terminate_chrome(pids, emit)
                in_use, _ = chrome_lock_status(path)
            if in_use:
                raise RuntimeError(
                    f"Chrome is still using {path}. Quit it, enable 'Restart Chrome', "
                    "or launch Chrome with --remote-debugging-port and use CDP."
                )
        opts.add_argument(f"--user-data-dir={path}")
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
                if emit:
                    emit("log", f"DISPLAY unset; using {display}")
            elif not headless:
                raise RuntimeError("No usable X display found. Use headless mode or set DISPLAY.")
        if headless:
            opts.add_argument("--headless=new")
        if emit:
            emit("log", f"Launching Chrome with profile {path}")

    opts.set_capability("pageLoadStrategy", "normal")
    driver = webdriver.Chrome(options=opts)
    driver.set_page_load_timeout(30)
    return driver


def body_text(driver) -> str:
    try:
        return driver.execute_script(
            "return document.body ? (document.body.innerText || '') : ''"
        ) or ""
    except WebDriverException:
        return ""


def page_guard(driver) -> str | None:
    """Return an abort reason, or None if the page looks normal."""
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
    """Poll until a usable state appears; blank pages become 'unavailable' fast."""
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


def classify(driver, mode: str) -> str:
    """Classify the current profile for the selected mode."""
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
    return "unknown"


def process_profile(driver, username: str, mode: str, apply: bool, emit=None) -> tuple[str, str]:
    """Visit one profile. Returns (status, detail). Emits detail log lines."""
    def say(text: str):
        if emit:
            emit("log", text)

    if username.startswith("__deleted__"):
        return "not_found", "marked deleted in input file"

    try:
        driver.get(f"https://www.instagram.com/{username}/")
    except TimeoutException:
        return "error", "page load timed out"

    state = wait_for_state(driver, lambda d: classify(d, mode))
    time.sleep(0.4)

    if state.startswith("abort:"):
        return "abort", state.split("abort:", 1)[1]
    if state != ("requested" if mode == "cancel" else "following"):
        return state, ""

    if not apply:
        return ("would_cancel" if mode == "cancel" else "would_unfollow"), "dry run"

    if mode == "cancel":
        try:
            clicked = driver.execute_script(JS_CLICK_REQUESTED)
        except WebDriverException as exc:
            return "error", f"click failed: {type(exc).__name__}"
        if not clicked:
            return "error", "Requested button disappeared before click"
        say(f"  clicked {clicked!r}")
        time.sleep(0.9)
        guard = page_guard(driver)
        if guard:
            return "abort", guard
        try:
            confirm = driver.execute_script(JS_CLICK_CANCEL_CONFIRM)
        except WebDriverException:
            confirm = None
        if confirm:
            say(f"  confirmed {confirm!r}")
            time.sleep(1.2)
    else:
        try:
            clicked = driver.execute_script(JS_CLICK_FOLLOWING)
        except WebDriverException as exc:
            return "error", f"click failed: {type(exc).__name__}"
        if not clicked:
            return "error", "Following button disappeared before click"
        say(f"  clicked {clicked!r}")
        time.sleep(1.0)
        guard = page_guard(driver)
        if guard:
            return "abort", guard
        try:
            confirm = driver.execute_script(JS_CLICK_UNFOLLOW_CONFIRM)
        except WebDriverException:
            confirm = None
        if confirm:
            say(f"  confirmed {confirm!r}")
            time.sleep(1.2)

    guard = page_guard(driver)
    if guard:
        return "abort", guard

    target = "requested" if mode == "cancel" else "following"
    try:
        WebDriverWait(driver, 8).until(
            lambda d: classify(d, mode) != target or page_guard(d) is not None
        )
    except TimeoutException:
        pass
    final = classify(driver, mode)
    if final.startswith("abort:"):
        return "abort", final.split("abort:", 1)[1]
    success = {"follow", "follow_back", "not_found"}
    if final in success:
        return ("cancelled" if mode == "cancel" else "unfollowed"), ""
    if final == target:
        return "unverified", "button did not change; do NOT click again without checking"
    return "unverified", f"unexpected post-click state: {final}"
