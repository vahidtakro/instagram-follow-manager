"""Background worker with start / pause / resume / stop support."""

from __future__ import annotations

import queue
import random
import threading
import time
from dataclasses import dataclass, field

from . import social
from .browser import make_driver, page_guard, process_profile
from .state import StateStore

MODES = {
    "cancel": "Cancel outgoing follow requests",
    "unfollow": "Unfollow non-followers",
    "engage": "Like feed posts / reels (human-paced)",
    "stories": "Watch stories (human-paced)",
}

SOCIAL_MODES = {"engage", "stories"}

DEFAULT_STATE = {
    "cancel": "cancel_state.json",
    "unfollow": "unfollow_state.json",
    "engage": "engage_state.json",
    "stories": "stories_state.json",
}


@dataclass
class Options:
    mode: str = "cancel"
    apply: bool = False
    limit: int = 20
    min_delay: float = 25.0
    max_delay: float = 45.0
    profile: str = "~/.config/chrome-instagram"
    cdp: str = ""
    headless: bool = False
    restart_chrome: bool = False
    retry_errors: bool = False
    driver_path: str = ""
    random_delay: bool = True
    # Human-paced social options
    like_chance: int = 35
    dwell_min: float = 4.0
    dwell_max: float = 12.0
    skip_chance: int = 30
    story_min: float = 3.0
    story_max: float = 9.0
    watch_reels: bool = True
    watch_stories: bool = False
    follow_only: bool = False


@dataclass
class Engine:
    """Runs automation in a worker thread and emits events to a queue."""

    options: Options = field(default_factory=Options)
    state_store: StateStore | None = None
    usernames: list[str] = field(default_factory=list)

    def __post_init__(self):
        self.events: queue.Queue = queue.Queue()
        self._pause = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.running = False

    # -- public control -------------------------------------------------
    def start(self) -> bool:
        if self.running:
            return False
        if not self.usernames and self.options.mode not in SOCIAL_MODES:
            self.emit("log", "Load a username file first.")
            return False
        if self.state_store is None:
            self.emit("log", "No state store configured.")
            return False
        self._pause.clear()
        self._stop.clear()
        self.running = True
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()
        return True

    def pause(self) -> None:
        if self.running:
            self._pause.set()
            self.emit("control", "paused")

    def resume(self) -> None:
        self._pause.clear()
        if self.running:
            self.emit("control", "running")

    def stop(self) -> None:
        self._stop.set()
        self._pause.clear()
        if self.running:
            self.emit("control", "stopping")

    def join(self, timeout: float = 10.0) -> None:
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout)

    def emit(self, kind: str, *args) -> None:
        self.events.put((kind, args))

    # -- pacing ---------------------------------------------------------
    def _wait_while_paused(self) -> bool:
        """Return False if stopped."""
        while self._pause.is_set() and not self._stop.is_set():
            time.sleep(0.2)
        return not self._stop.is_set()

    def _sleep(self, seconds: float) -> bool:
        """Interruptible pause that also respects the pause button."""
        deadline = time.monotonic() + seconds
        while True:
            if self._stop.is_set():
                return False
            if self._pause.is_set():
                time.sleep(0.2)
                deadline += 0.2  # paused time does not count toward the delay
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return True
            time.sleep(min(0.25, remaining))

    def _human_wait(self, low: float, high: float) -> float:
        """Pick a varied wait; occasionally add a longer 'lost in the feed' pause."""
        if high <= low:
            spread = max(1.5, low * 0.3)
            low, high = max(0.0, low - spread), high + spread
        value = random.uniform(low, high)
        if random.random() < 0.1:
            value *= random.uniform(1.3, 1.9)
        return value

    # -- main worker ----------------------------------------------------
    def _worker(self) -> None:
        opt = self.options
        state = self.state_store
        assert state is not None

        social_mode = opt.mode in SOCIAL_MODES
        if social_mode:
            total = max(0, opt.limit)
            self.emit("started", total)
            self.emit("log", f"{MODES.get(opt.mode, opt.mode)}: up to {total} item(s).")
            self.emit("log", "DRY RUN - no clicks, short viewing times." if not opt.apply
                      else "APPLY MODE - likes enabled.")
            if opt.follow_only and self.usernames:
                self.emit("log", f"Restricting likes to {len(self.usernames)} loaded username(s).")
        else:
            todo = [u for u in self.usernames if not state.handled(u, opt.retry_errors)]
            for name in todo:
                if name.startswith("__deleted__"):
                    state.record(name, "not_found", "marked deleted in input file")
            todo = [u for u in todo if not u.startswith("__deleted__")]
            todo = todo[: max(0, opt.limit)]
            total = len(todo)
            self.emit("started", total)
            if total == 0:
                self.emit("log", "Nothing to do - all usernames are already handled.")
                self.emit("done", {}, False, 0, 0)
                self.running = False
                return
            self.emit("log", f"{MODES.get(opt.mode, opt.mode)}: {total} profile(s) queued.")
            self.emit("log", "DRY RUN - no clicks." if not opt.apply else "APPLY MODE - actions enabled.")

        driver = None
        counts: dict[str, int] = {}
        done_count = 0
        aborted = False
        reason = ""

        try:
            driver = make_driver(
                opt.profile, opt.cdp or None, opt.headless, opt.restart_chrome,
                lambda kind, *args: self.emit(kind, *args),
                driver_path=opt.driver_path or None,
            )
            driver.get("https://www.instagram.com/")
            time.sleep(2)
            guard = page_guard(driver)
            if guard:
                raise RuntimeError(f"Preflight failed: {guard}")
            if self._stop.is_set():
                return

            if social_mode:
                counts, done_count, aborted, reason = self._social_worker(driver, state)
            else:
                counts, done_count, aborted, reason = self._profile_worker(driver, state, todo)
        except Exception as exc:  # noqa: BLE001 - surface any failure to the GUI
            aborted = True
            reason = f"{type(exc).__name__}: {exc}"
            self.emit("log", f"ERROR: {reason}")
        finally:
            if driver is not None:
                try:
                    driver.quit()
                except Exception:
                    pass
            self.emit("done", counts, aborted, done_count, total, reason)
            self.running = False

    # -- profile-based modes (existing behaviour) -----------------------
    def _profile_worker(self, driver, state, todo) -> tuple[dict, int, bool, str]:
        opt = self.options
        counts: dict[str, int] = {}
        done_count = 0
        for index, username in enumerate(todo, 1):
            if not self._wait_while_paused():
                break
            self.emit("current", username, index, len(todo))
            self.emit("log", f"[{index}/{len(todo)}] {username}")
            status, detail = process_profile(
                driver, username, opt.mode, opt.apply,
                lambda kind, *args: self.emit(kind, *args),
            )
            entry = state.record(username, status, detail)
            counts[status] = counts.get(status, 0) + 1
            done_count += 1
            self.emit("log", f"  -> {status}" + (f" ({detail})" if detail else ""))
            self.emit("row", username, status, entry.get("detail", ""), entry.get("time", ""))
            self.emit("progress", done_count, len(todo))
            if status == "abort":
                self.emit("log", f"STOPPING FOR SAFETY: {detail}")
                return counts, done_count, True, detail
            if index < len(todo):
                if opt.random_delay:
                    low, high = opt.min_delay, opt.max_delay
                    if high <= low:
                        spread = max(2.0, low * 0.3)
                        low, high = max(0.0, low - spread), high + spread
                    pause = random.uniform(low, high)
                    self.emit("log", f"  waiting {pause:.0f}s (random {low:.0f}-{high:.0f}s)")
                else:
                    pause = opt.min_delay
                    self.emit("log", f"  waiting {pause:.0f}s (fixed)")
                if not self._sleep(pause):
                    break
        return counts, done_count, False, ""

    # -- human-paced social modes ---------------------------------------
    def _social_worker(self, driver, state) -> tuple[dict, int, bool, str]:
        opt = self.options
        counts: dict[str, int] = {}
        done = 0
        total = max(0, opt.limit)
        aborted = False
        reason = ""

        def record(author: str, status: str, detail: str = "") -> None:
            nonlocal done
            entry = state.record(author, status, detail)
            counts[status] = counts.get(status, 0) + 1
            done += 1
            self.emit("log", f"  -> @{author}: {status}" + (f" ({detail})" if detail else ""))
            self.emit("row", author, status, entry.get("detail", ""), entry.get("time", ""))
            self.emit("progress", done, total)

        def guard_ok() -> bool:
            nonlocal aborted, reason
            guard = page_guard(driver)
            if guard:
                aborted = True
                reason = guard
                self.emit("log", f"STOPPING FOR SAFETY: {guard}")
                return False
            return True

        def wait(low: float, high: float) -> bool:
            value = self._human_wait(low, high)
            if not opt.apply:
                value = min(value, 1.0)  # keep dry runs quick
            self.emit("log", f"  dwelling {value:.0f}s" + (" (dry run)" if not opt.apply else ""))
            return self._sleep(value)

        try:
            if opt.mode == "stories" or opt.watch_stories:
                cap = total if opt.mode == "stories" else max(2, total // 3)
                self._story_pass(driver, state, counts, cap, guard_ok, wait, record)
                if aborted or self._stop.is_set():
                    return counts, done, aborted, reason

            if opt.mode == "engage":
                self._feed_pass(driver, state, counts, total - done, guard_ok, wait, record)
                if not aborted and not self._stop.is_set() and opt.watch_reels and done < total:
                    self._reel_pass(driver, state, counts, total - done, guard_ok, wait, record)
        finally:
            self.emit("progress", done, total)
        return counts, done, aborted, reason

    def _story_pass(self, driver, state, counts, cap, guard_ok, wait, record) -> int:
        opt = self.options
        if cap <= 0:
            return 0
        self.emit("log", "Opening stories tray...")
        driver.get("https://www.instagram.com/")
        self._sleep(2.0)
        if not guard_ok():
            return 0
        users = social.story_users(driver)
        random.shuffle(users)  # don't always start with the same person
        if not users:
            self.emit("log", "No stories available right now.")
            return 0
        self.emit("log", f"Found {len(users)} story author(s).")
        done = 0
        for user in users:
            if done >= cap or self._stop.is_set() or not self._wait_while_paused():
                break
            if not social.open_story(driver, user):
                continue
            self._sleep(1.0)
            if not social.story_open(driver):
                continue
            for _ in range(10):  # safety cap per author
                if done >= cap or self._stop.is_set() or not self._wait_while_paused():
                    break
                if not social.story_open(driver) or not guard_ok():
                    break
                author = social.story_author(driver) or user
                if state.handled(author):
                    if not social.story_next(driver):
                        break
                    continue
                self.emit("current", author, done + 1, cap)
                if random.random() < opt.skip_chance / 100.0:
                    low, high = opt.story_min * 0.15, opt.story_max * 0.4
                    label = "skipping"
                else:
                    low, high = opt.story_min, opt.story_max
                    label = "watching"
                self.emit("log", f"  story @{author}: {label}")
                if opt.apply:
                    social.wander_mouse(driver)
                if not wait(low, high):
                    break
                if not guard_ok():
                    break
                record(author, "viewed" if opt.apply else "would_view", label)
                done += 1
                if not social.story_next(driver):
                    break
            social.story_close(driver)
            self._sleep(random.uniform(0.6, 1.6))
        return done

    def _feed_pass(self, driver, state, counts, cap, guard_ok, wait, record) -> int:
        opt = self.options
        if cap <= 0:
            return 0
        self.emit("log", "Browsing home feed...")
        driver.get("https://www.instagram.com/")
        self._sleep(2.0)
        if not guard_ok():
            return 0
        allowed = set(self.usernames) if opt.follow_only and self.usernames else None
        if allowed:
            self.emit("log", f"Only liking posts from {len(allowed)} loaded username(s).")
        seen: set[str] = set()
        done = 0
        idle_rounds = 0
        while done < cap and not self._stop.is_set() and idle_rounds < 4:
            if not self._wait_while_paused() or not guard_ok():
                break
            posts = [p for p in social.feed_posts(driver)
                     if p.get("postId") and p.get("author") and p["postId"] not in seen]
            if not posts:
                social.scroll_feed(driver)
                self._sleep(random.uniform(1.0, 2.6))
                idle_rounds += 1
                continue
            idle_rounds = 0
            for post in posts:
                if done >= cap or self._stop.is_set() or not self._wait_while_paused():
                    break
                author = post["author"]
                seen.add(post["postId"])
                if state.handled(author):
                    continue
                if allowed is not None and author not in allowed:
                    continue
                self.emit("current", author, done + 1, cap)
                kind = post.get("kind", "post")
                dwell = self._human_wait(opt.dwell_min, opt.dwell_max)
                if not opt.apply:
                    dwell = min(dwell, 1.0)
                self.emit("log", f"  looking at {kind} by @{author} for {dwell:.0f}s")
                if opt.apply:
                    social.wander_mouse(driver)
                if not self._sleep(dwell) or not guard_ok():
                    break
                will_like = (not post.get("liked")
                             and random.random() < opt.like_chance / 100.0)
                if will_like and opt.apply:
                    before = social.click_article_like(driver, post["index"])
                    self._sleep(0.8)
                    after = social.article_like_state(driver, post["index"])
                    status = "liked" if before == "Like" and after == "Unlike" else "unverified"
                    detail = "" if status == "liked" else "button state uncertain - not retried"
                elif will_like:
                    status, detail = "would_like", "dry run"
                else:
                    status, detail = "viewed", f"left unliked ({kind})"
                record(author, status, detail)
                done += 1
                if random.random() < 0.08:
                    self._sleep(random.uniform(1.5, 3.0))
            social.scroll_feed(driver)
            self._sleep(random.uniform(1.0, 2.5))
        return done

    def _reel_pass(self, driver, state, counts, cap, guard_ok, wait, record) -> int:
        opt = self.options
        if cap <= 0:
            return 0
        self.emit("log", "Browsing reels...")
        driver.get("https://www.instagram.com/reels/")
        self._sleep(3.0)
        done = 0
        for _ in range(cap):
            if self._stop.is_set() or not self._wait_while_paused() or not guard_ok():
                break
            info = social.active_reel(driver)
            if not info:
                self.emit("log", "No reel detected.")
                break
            author = info.get("author") or "unknown_reel"
            if state.handled(author):
                if not social.next_reel(driver):
                    break
                self._sleep(random.uniform(0.6, 1.4))
                continue
            self.emit("current", author, done + 1, cap)
            dwell = self._human_wait(opt.dwell_min, opt.dwell_max)
            if not opt.apply:
                dwell = min(dwell, 1.0)
            self.emit("log", f"  watching reel by @{author} for {dwell:.0f}s")
            if opt.apply:
                social.wander_mouse(driver)
            if not self._sleep(dwell) or not guard_ok():
                break
            will_like = (not info.get("liked")
                         and random.random() < opt.like_chance / 100.0)
            if will_like and opt.apply:
                before = social.click_reel_like(driver)
                self._sleep(0.8)
                after = social.reel_like_state(driver)
                status = "liked" if before == "Like" and after == "Unlike" else "unverified"
                detail = "" if status == "liked" else "button state uncertain - not retried"
            elif will_like:
                status, detail = "would_like", "dry run"
            else:
                status, detail = "viewed", "left unliked (reel)"
            record(author, status, detail)
            done += 1
            if not social.next_reel(driver):
                break
            self._sleep(random.uniform(0.8, 1.8))
        return done
