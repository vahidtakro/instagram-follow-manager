"""Background worker with start / pause / resume / stop support."""

from __future__ import annotations

import queue
import random
import threading
import time
from dataclasses import dataclass, field

from .browser import make_driver, page_guard, process_profile
from .state import StateStore

MODES = {
    "cancel": "Cancel outgoing follow requests",
    "unfollow": "Unfollow non-followers",
}

DEFAULT_STATE = {
    "cancel": "cancel_state.json",
    "unfollow": "unfollow_state.json",
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
        if not self.usernames:
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

    # -- worker ---------------------------------------------------------
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

    def _worker(self) -> None:
        opt = self.options
        state = self.state_store
        assert state is not None

        todo = [u for u in self.usernames if not state.handled(u, opt.retry_errors)]
        # Pre-record deleted placeholders so they appear in history.
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

            for index, username in enumerate(todo, 1):
                if not self._wait_while_paused():
                    break
                self.emit("current", username, index, total)
                self.emit("log", f"[{index}/{total}] {username}")

                status, detail = process_profile(
                    driver, username, opt.mode, opt.apply,
                    lambda kind, *args: self.emit(kind, *args),
                )
                entry = state.record(username, status, detail)
                counts[status] = counts.get(status, 0) + 1
                done_count += 1
                self.emit("log", f"  -> {status}" + (f" ({detail})" if detail else ""))
                self.emit("row", username, status, entry.get("detail", ""), entry.get("time", ""))
                self.emit("progress", done_count, total)

                if status == "abort":
                    aborted = True
                    reason = detail
                    self.emit("log", f"STOPPING FOR SAFETY: {detail}")
                    break

                if index < total:
                    if opt.random_delay:
                        low, high = opt.min_delay, opt.max_delay
                        if high <= low:
                            # A single entered value still gets natural jitter.
                            spread = max(2.0, low * 0.3)
                            low, high = max(0.0, low - spread), high + spread
                        pause = random.uniform(low, high)
                        self.emit("log", f"  waiting {pause:.0f}s (random {low:.0f}-{high:.0f}s)")
                    else:
                        pause = opt.min_delay
                        self.emit("log", f"  waiting {pause:.0f}s (fixed)")
                    if not self._sleep(pause):
                        break
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
