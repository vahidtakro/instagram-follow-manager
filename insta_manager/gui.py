"""Tkinter desktop GUI for the Instagram Follow Manager."""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .engine import DEFAULT_STATE, MODES, SOCIAL_MODES, Engine, Options
from .inputs import load_usernames
from .state import StateStore

STATE_DIR = Path.home() / ".instagram-follow-manager"
GITHUB_URL = "https://github.com/vahidtakro/instagram-follow-manager"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Instagram Follow Manager")
        self.geometry("1120x780")
        self.minsize(900, 640)

        self.engine = Engine()
        self.usernames: list[str] = []
        self.preview_names: list[str] = []
        self.warnings: list[str] = []
        self.state_dir = STATE_DIR
        self.state_dir.mkdir(parents=True, exist_ok=True)

        self.mode_var = tk.StringVar(value="cancel")
        self.file_var = tk.StringVar()
        self.profile_var = tk.StringVar(value=str(Path.home() / ".config" / "chrome-instagram"))
        self.cdp_var = tk.StringVar()
        self.driver_var = tk.StringVar()
        self.random_var = tk.BooleanVar(value=True)
        self.dry_var = tk.BooleanVar(value=True)
        self.headless_var = tk.BooleanVar(value=False)
        self.restart_var = tk.BooleanVar(value=True)
        self.retry_var = tk.BooleanVar(value=False)
        self.limit_var = tk.IntVar(value=20)
        self.min_var = tk.DoubleVar(value=25.0)
        self.max_var = tk.DoubleVar(value=45.0)
        # Human-paced social settings
        self.like_chance_var = tk.IntVar(value=35)
        self.dwell_min_var = tk.DoubleVar(value=4.0)
        self.dwell_max_var = tk.DoubleVar(value=12.0)
        self.skip_chance_var = tk.IntVar(value=30)
        self.story_min_var = tk.DoubleVar(value=3.0)
        self.story_max_var = tk.DoubleVar(value=9.0)
        self.watch_reels_var = tk.BooleanVar(value=True)
        self.watch_stories_var = tk.BooleanVar(value=False)
        self.history_mode_var = tk.StringVar(value="cancel")
        self.search_var = tk.StringVar()
        self.status_filter_var = tk.StringVar(value="all")

        self._build()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(120, self._poll_events)
        self._log("Ready. Load a .txt or .json file, review the list, then press Start.")
        self._refresh_history()

    # ---------------------------------------------------------------- UI
    def _build(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        menubar = tk.Menu(self)
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Open input file...", command=self._browse_input)
        file_menu.add_command(label="Export history CSV...", command=self._export_csv)
        file_menu.add_separator()
        file_menu.add_command(label="Quit", command=self._on_close)
        menubar.add_cascade(label="File", menu=file_menu)
        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="About / safety", command=lambda: self.notebook.select(2))
        menubar.add_cascade(label="Help", menu=help_menu)
        self.config(menu=menubar)

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=10)

        self.run_tab = ttk.Frame(self.notebook)
        self.history_tab = ttk.Frame(self.notebook)
        self.about_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.run_tab, text="  Run  ")
        self.notebook.add(self.history_tab, text="  History  ")
        self.notebook.add(self.about_tab, text="  About / Safety  ")

        self._build_run_tab()
        self._build_history_tab()
        self._build_about_tab()

    def _build_run_tab(self) -> None:
        top = ttk.LabelFrame(self.run_tab, text="1. Task and input", padding=10)
        top.pack(fill="x", padx=8, pady=6)

        ttk.Label(top, text="Task:").grid(row=0, column=0, sticky="w")
        mode_combo = ttk.Combobox(
            top, textvariable=self.mode_var, values=list(MODES), state="readonly", width=38
        )
        mode_combo.grid(row=0, column=1, sticky="w", padx=6)
        mode_combo.bind("<<ComboboxSelected>>", self._on_mode_change)
        ttk.Label(top, text="(both tasks accept .txt, .json or .csv)").grid(row=0, column=2, sticky="w")

        ttk.Label(top, text="Input file:").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(top, textvariable=self.file_var, width=72).grid(row=1, column=1, sticky="ew", pady=(8, 0))
        ttk.Button(top, text="Browse...", command=self._browse_input).grid(row=1, column=2, padx=6, pady=(8, 0))
        ttk.Button(top, text="Load", command=self._load_input).grid(row=1, column=3, pady=(8, 0))
        top.columnconfigure(1, weight=1)

        self.file_info = ttk.Label(top, text="No file loaded")
        self.file_info.grid(row=2, column=1, columnspan=3, sticky="w", pady=(6, 0))

        preview = ttk.LabelFrame(self.run_tab, text="Username preview", padding=6)
        preview.pack(fill="both", expand=False, padx=8, pady=6)
        self.preview_list = tk.Listbox(preview, height=6, activestyle="none")
        scroll = ttk.Scrollbar(preview, orient="vertical", command=self.preview_list.yview)
        self.preview_list.config(yscrollcommand=scroll.set)
        self.preview_list.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        preview_actions = ttk.Frame(preview)
        preview_actions.pack(fill="x", pady=(4, 0))
        ttk.Button(preview_actions, text="Remove selected",
                   command=self._remove_selected).pack(side="left")

        opts = ttk.LabelFrame(self.run_tab, text="2. Settings", padding=10)
        opts.pack(fill="x", padx=8, pady=6)

        ttk.Checkbutton(opts, text="Dry run (no clicks)", variable=self.dry_var).grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(opts, text="Headless Chrome", variable=self.headless_var).grid(row=0, column=1, sticky="w")
        ttk.Checkbutton(opts, text="Restart Chrome if in use", variable=self.restart_var).grid(row=0, column=2, sticky="w")
        ttk.Checkbutton(opts, text="Retry errors / unavailable", variable=self.retry_var).grid(row=0, column=3, sticky="w")
        ttk.Checkbutton(opts, text="Random delay (jitter)", variable=self.random_var).grid(row=0, column=4, sticky="w")

        ttk.Label(opts, text="Max items this run:").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Spinbox(opts, from_=1, to=2000, textvariable=self.limit_var, width=8).grid(row=1, column=1, sticky="w", pady=(8, 0))
        ttk.Label(opts, text="Delay from (s):").grid(row=1, column=2, sticky="w", pady=(8, 0))
        ttk.Spinbox(opts, from_=0, to=600, increment=5, textvariable=self.min_var, width=8).grid(row=1, column=3, sticky="w", pady=(8, 0))
        ttk.Label(opts, text="to (s):").grid(row=1, column=4, sticky="w", pady=(8, 0))
        ttk.Spinbox(opts, from_=0, to=600, increment=5, textvariable=self.max_var, width=8).grid(row=1, column=5, sticky="w", pady=(8, 0))

        ttk.Label(opts, text="Chrome profile:").grid(row=2, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(opts, textvariable=self.profile_var, width=58).grid(row=2, column=1, columnspan=3, sticky="ew", pady=(8, 0))
        ttk.Button(opts, text="Browse...", command=self._browse_profile).grid(row=2, column=4, padx=4, pady=(8, 0))
        ttk.Label(opts, text="CDP host:port (optional):").grid(row=3, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(opts, textvariable=self.cdp_var, width=22).grid(row=3, column=1, sticky="w", pady=(6, 0))
        ttk.Label(opts, text="ChromeDriver path (auto if blank):").grid(row=4, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(opts, textvariable=self.driver_var, width=58).grid(row=4, column=1, columnspan=3, sticky="ew", pady=(6, 0))
        opts.columnconfigure(1, weight=1)

        social = ttk.LabelFrame(self.run_tab, text="3. Human-paced behaviour (feed / reels / stories)", padding=10)
        social.pack(fill="x", padx=8, pady=6)
        ttk.Label(social, text="Like chance %:").grid(row=0, column=0, sticky="w")
        ttk.Spinbox(social, from_=0, to=100, textvariable=self.like_chance_var, width=7).grid(row=0, column=1, sticky="w")
        ttk.Label(social, text="Look at post from/to (s):").grid(row=0, column=2, sticky="w", padx=(10, 2))
        ttk.Spinbox(social, from_=1, to=120, increment=1, textvariable=self.dwell_min_var, width=7).grid(row=0, column=3, sticky="w")
        ttk.Spinbox(social, from_=1, to=180, increment=1, textvariable=self.dwell_max_var, width=7).grid(row=0, column=4, sticky="w")
        ttk.Label(social, text="Story skip %:").grid(row=0, column=5, sticky="w", padx=(10, 2))
        ttk.Spinbox(social, from_=0, to=100, textvariable=self.skip_chance_var, width=7).grid(row=0, column=6, sticky="w")
        ttk.Label(social, text="Watch story from/to (s):").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Spinbox(social, from_=1, to=60, textvariable=self.story_min_var, width=7).grid(row=1, column=1, sticky="w", pady=(8, 0))
        ttk.Spinbox(social, from_=1, to=90, textvariable=self.story_max_var, width=7).grid(row=1, column=2, sticky="w", pady=(8, 0))
        ttk.Checkbutton(social, text="Browse reels too", variable=self.watch_reels_var).grid(row=1, column=3, sticky="w", pady=(8, 0))
        ttk.Checkbutton(social, text="Also watch stories first", variable=self.watch_stories_var).grid(row=1, column=4, columnspan=3, sticky="w", pady=(8, 0))
        ttk.Label(social, text="Tip: load a .txt of usernames to restrict likes to those authors. "
                              "Leave it empty to like any feed post.").grid(row=2, column=0, columnspan=7, sticky="w", pady=(6, 0))

        controls = ttk.LabelFrame(self.run_tab, text="4. Control", padding=10)
        controls.pack(fill="x", padx=8, pady=6)
        self.start_btn = ttk.Button(controls, text="Start", command=self._start)
        self.pause_btn = ttk.Button(controls, text="Pause", command=self._pause)
        self.resume_btn = ttk.Button(controls, text="Continue", command=self._resume)
        self.stop_btn = ttk.Button(controls, text="Stop", command=self._stop)
        self.reset_btn = ttk.Button(controls, text="Reset state", command=self._reset_state)
        for i, btn in enumerate((self.start_btn, self.pause_btn, self.resume_btn,
                                 self.stop_btn, self.reset_btn)):
            btn.grid(row=0, column=i, padx=4)

        status_frame = ttk.Frame(self.run_tab)
        status_frame.pack(fill="x", padx=8, pady=4)
        self.status_label = ttk.Label(status_frame, text="Status: idle")
        self.status_label.pack(side="left")
        self.current_label = ttk.Label(status_frame, text="")
        self.current_label.pack(side="right")
        self.progress = ttk.Progressbar(self.run_tab, mode="determinate", maximum=100)
        self.progress.pack(fill="x", padx=8, pady=4)

        log_frame = ttk.LabelFrame(self.run_tab, text="Live log", padding=4)
        log_frame.pack(fill="both", expand=True, padx=8, pady=6)
        self.log_text = tk.Text(log_frame, height=12, wrap="none", font="TkFixedFont")
        log_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        self.log_text.config(yscrollcommand=log_scroll.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        log_scroll.pack(side="right", fill="y")
        self.log_text.tag_config("error", foreground="#c0392b")
        self.log_text.tag_config("good", foreground="#1e8449")
        self.log_text.tag_config("warn", foreground="#b9770e")

    def _build_history_tab(self) -> None:
        bar = ttk.Frame(self.history_tab)
        bar.pack(fill="x", padx=8, pady=8)
        ttk.Label(bar, text="Task:").pack(side="left")
        ttk.Combobox(bar, textvariable=self.history_mode_var, values=list(MODES),
                     state="readonly", width=38).pack(side="left", padx=4)
        ttk.Button(bar, text="Refresh", command=self._refresh_history).pack(side="left", padx=8)
        ttk.Button(bar, text="Export CSV...", command=self._export_csv).pack(side="left")
        ttk.Button(bar, text="Open folder", command=self._open_state_folder).pack(side="left", padx=6)
        ttk.Label(bar, text="Search:").pack(side="left", padx=(12, 2))
        ttk.Entry(bar, textvariable=self.search_var, width=24).pack(side="left")
        ttk.Label(bar, text="Status:").pack(side="left", padx=(12, 2))
        self.status_combo = ttk.Combobox(bar, textvariable=self.status_filter_var,
                                         values=["all"], state="readonly", width=22)
        self.status_combo.pack(side="left")

        self.summary_label = ttk.Label(self.history_tab, text="")
        self.summary_label.pack(fill="x", padx=8)

        tree_frame = ttk.Frame(self.history_tab)
        tree_frame.pack(fill="both", expand=True, padx=8, pady=6)
        columns = ("username", "status", "detail", "time")
        self.history_tree = ttk.Treeview(tree_frame, columns=columns, show="headings", height=18)
        for col, heading, width in (
            ("username", "Username", 230),
            ("status", "Status", 220),
            ("detail", "Detail", 350),
            ("time", "Time (UTC)", 190),
        ):
            self.history_tree.heading(col, text=heading)
            self.history_tree.column(col, width=width, anchor="w")
        tscroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.history_tree.yview)
        self.history_tree.config(yscrollcommand=tscroll.set)
        self.history_tree.pack(side="left", fill="both", expand=True)
        tscroll.pack(side="right", fill="y")

    def _build_about_tab(self) -> None:
        text = (
            "INSTAGRAM FOLLOW MANAGER\n"
            "=========================\n\n"
            "Four tasks in one app:\n"
            "  * Cancel outgoing follow requests (requests you sent)\n"
            "  * Unfollow non-followers (people you follow who don't follow you)\n"
            "  * Engage: browse home feed / reels and like a random selection\n"
            "  * Stories: open stories, watch some, skip others at random\n\n"
            "HUMAN-PACED MODES\n"
            "  Engage strolls the feed, spends a variable time looking at each post,\n"
            "  likes only a percentage of them, scrolls irregularly, and moves the\n"
            "  mouse a little. It never likes the same author twice in one state.\n"
            "  Load a .txt of usernames to restrict likes to those people; leave it\n"
            "  empty to like any post in your feed.\n"
            "  Stories picks authors in random order, watches or skips each story\n"
            "  with random durations, and moves on when a person's stories end.\n\n"
            "INPUT FILES\n"
            "  All tasks accept a plain .txt file with one username per line,\n"
            "  or the original Instagram JSON export. Blank lines and lines\n"
            "  starting with # are ignored. @names and profile URLs are accepted.\n"
            "  The two social modes do not require a file.\n\n"
            "SAFETY\n"
            "  - Dry run is ON by default. Turn it off only when you are ready.\n"
            "  - Only profiles with a matching button are touched. Accepted requests\n"
            "    and people who follow you back are never unfollowed.\n"
            "  - Pause / Continue / Stop are available while a batch is running.\n"
            "  - If an action's result is uncertain it is never retried automatically.\n"
            "  - Instagram rate-limits these actions. Keep the default delays and\n"
            "    work in small batches over several days.\n"
            "  - Random delay is ON: each wait varies between the two delay values.\n"
            "    One value alone still gets +/-30% jitter. Uncheck it for a fixed wait.\n\n"
            "CHROMEDRIVER\n"
            "  Detected automatically (explicit path, Selenium cache, then PATH).\n"
            "  Set 'ChromeDriver path' in Settings only if auto-detection fails.\n\n"
            "WHERE THINGS ARE SAVED\n"
            f"  State/history files: {self.state_dir}\n"
            "  They are local only and never uploaded.\n\n"
            "DISCLAIMER\n"
            "  Browser automation may violate Instagram's Terms of Use and can\n"
            "  lead to temporary restrictions. Use at your own risk.\n\n"
            f"Project: {GITHUB_URL}\n"
        )
        widget = tk.Text(self.about_tab, wrap="word", font="TkFixedFont", padx=12, pady=12)
        widget.insert("1.0", text)
        widget.config(state="disabled")
        widget.pack(fill="both", expand=True)

    # ------------------------------------------------------------- actions
    def _browse_input(self) -> None:
        path = filedialog.askopenfilename(
            title="Select username file",
            filetypes=[("Supported files", "*.txt *.json *.csv"), ("All files", "*.*")],
        )
        if path:
            self.file_var.set(path)
            self._load_input()

    def _browse_profile(self) -> None:
        path = filedialog.askdirectory(title="Select Chrome profile directory")
        if path:
            self.profile_var.set(path)

    def _load_input(self) -> None:
        path = self.file_var.get().strip()
        if not path:
            messagebox.showwarning("No file", "Choose an input file first.")
            return
        try:
            names, warnings = load_usernames(path)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Could not load file", str(exc))
            return
        self.usernames = names
        self.warnings = warnings
        self._render_preview()
        self.file_info.config(text=f"Loaded {len(names)} unique usernames" +
                              (f" ({len(warnings)} warnings)" if warnings else ""))
        self._log(f"Loaded {len(names)} usernames from {path}")
        for warning in warnings[:10]:
            self._log(f"  warning: {warning}", "warn")
        self._refresh_history()

    def _render_preview(self) -> None:
        self.preview_list.delete(0, "end")
        self.preview_names = list(self.usernames[:200])
        for name in self.preview_names:
            self.preview_list.insert("end", name)
        if len(self.usernames) > 200:
            self.preview_list.insert("end", f"... and {len(self.usernames) - 200} more")
        self.file_info.config(text=f"Loaded {len(self.usernames)} unique usernames" +
                              (f" ({len(self.warnings)} warnings)" if self.warnings else ""))

    def _remove_selected(self) -> None:
        selected = self.preview_list.curselection()
        if not selected:
            return
        for index in sorted(selected, reverse=True):
            if index >= len(self.preview_names):
                continue
            name = self.preview_names[index]
            if name in self.usernames:
                self.usernames.remove(name)
        self._render_preview()
        self._log(f"Removed {len(selected)} username(s) from this run.", "warn")

    def _open_state_folder(self) -> None:
        target = str(self.state_dir)
        try:
            if sys.platform == "win32":
                os.startfile(target)  # noqa: S606
            elif sys.platform == "darwin":
                subprocess.run(["open", target], check=False)
            else:
                subprocess.run(["xdg-open", target], check=False)
        except Exception as exc:  # noqa: BLE001
            messagebox.showinfo("History folder", f"Open this folder manually:\n{target}\n\n{exc}")

    def _on_mode_change(self, _event=None) -> None:
        """Give each task a sensible batch size, but let the user override it."""
        defaults = {"cancel": 20, "unfollow": 15, "engage": 50, "stories": 50}
        value = defaults.get(self.mode_var.get(), 20)
        self.limit_var.set(value)
        self._log(f"Batch size set to {value} for this task (edit 'Max items this run' to change).")
        self._refresh_history()

    def _runtime_options(self) -> Options:
        return Options(
            mode=self.mode_var.get(),
            apply=not self.dry_var.get(),
            limit=int(self.limit_var.get()),
            min_delay=float(self.min_var.get()),
            max_delay=float(self.max_var.get()),
            profile=self.profile_var.get(),
            cdp=self.cdp_var.get().strip(),
            headless=self.headless_var.get(),
            restart_chrome=self.restart_var.get(),
            retry_errors=self.retry_var.get(),
            driver_path=self.driver_var.get().strip(),
            random_delay=self.random_var.get(),
            like_chance=int(self.like_chance_var.get()),
            dwell_min=float(self.dwell_min_var.get()),
            dwell_max=float(self.dwell_max_var.get()),
            skip_chance=int(self.skip_chance_var.get()),
            story_min=float(self.story_min_var.get()),
            story_max=float(self.story_max_var.get()),
            watch_reels=self.watch_reels_var.get(),
            watch_stories=self.watch_stories_var.get(),
            follow_only=bool(self.usernames),
        )

    def _state_path(self, mode: str) -> Path:
        return self.state_dir / DEFAULT_STATE.get(mode, f"{mode}_state.json")

    def _start(self) -> None:
        opt = self._runtime_options()
        if not self.usernames and opt.mode not in SOCIAL_MODES:
            messagebox.showwarning("No usernames", "Load an input file first.")
            return
        if not opt.apply and not messagebox.askyesno(
            "Dry run", "Dry run is ON: nothing will be clicked. Continue?"
        ):
            return
        if opt.apply and not messagebox.askyesno(
            "Confirm", "APPLY MODE: this will change your account. Continue?"
        ):
            return

        state_path = self._state_path(opt.mode)
        self.engine.options = opt
        self.engine.state_store = StateStore(state_path)
        self.engine.usernames = self.usernames
        if self.engine.start():
            self._set_controls("running")
            self.progress["value"] = 0
            self._log("--- started ---", "good")
            self._refresh_history()

    def _pause(self) -> None:
        self.engine.pause()

    def _resume(self) -> None:
        self.engine.resume()

    def _stop(self) -> None:
        self.engine.stop()
        self._set_controls("stopping")

    def _reset_state(self) -> None:
        mode = self.mode_var.get()
        path = self._state_path(mode)
        if not messagebox.askyesno("Reset state", f"Delete all recorded history in\n{path}?"):
            return
        StateStore(path).reset()
        self._refresh_history()
        self._log("State file reset.", "warn")

    def _refresh_history(self) -> None:
        mode = self.history_mode_var.get()
        path = self._state_path(mode)
        store = StateStore(path)
        rows = store.rows()
        counts = store.counts()
        self.summary_label.config(text="  |  ".join(f"{k}: {v}" for k, v in sorted(counts.items())) or "No history yet")

        statuses = ["all"] + sorted(counts)
        self.status_combo.config(values=statuses)
        if self.status_filter_var.get() not in statuses:
            self.status_filter_var.set("all")

        query = self.search_var.get().strip().lower()
        status_filter = self.status_filter_var.get()
        self.history_tree.delete(*self.history_tree.get_children())
        for row in rows:
            if status_filter != "all" and row["status"] != status_filter:
                continue
            if query and query not in row["username"].lower() and query not in row["detail"].lower():
                continue
            self.history_tree.insert("", "end", values=(
                row["username"], row["label"], row["detail"], row["time"][:19].replace("T", " ")
            ))

    def _export_csv(self) -> None:
        mode = self.history_mode_var.get()
        path = self._state_path(mode)
        target = filedialog.asksaveasfilename(
            title="Export history", defaultextension=".csv",
            filetypes=[("CSV files", "*.csv")], initialfile=f"{mode}_history.csv",
        )
        if target:
            StateStore(path).export_csv(target)
            self._log(f"Exported history to {target}", "good")

    # ------------------------------------------------------------- events
    def _log(self, text: str, tag: str = "") -> None:
        self.log_text.config(state="normal")
        self.log_text.insert("end", text + "\n", tag)
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    def _set_controls(self, state: str) -> None:
        self.start_btn.config(state="normal" if state == "idle" else "disabled")
        self.pause_btn.config(state="normal" if state == "running" else "disabled")
        self.resume_btn.config(state="normal" if state == "paused" else "disabled")
        self.stop_btn.config(state="normal" if state in {"running", "paused", "stopping"} else "disabled")
        self.reset_btn.config(state="normal" if state == "idle" else "disabled")
        labels = {
            "idle": "idle", "running": "running", "paused": "paused",
            "stopping": "stopping...", "done": "finished",
        }
        self.status_label.config(text=f"Status: {labels.get(state, state)}")

    def _poll_events(self) -> None:
        try:
            while True:
                kind, args = self.engine.events.get_nowait()
                if kind == "log":
                    text = str(args[0])
                    tag = "error" if "ERROR" in text or "STOPPING" in text else (
                        "good" if text.startswith("---") or "cancelled" in text or "unfollowed" in text else
                        "warn" if "warning" in text or "dry run" in text.lower() else "")
                    self._log(text, tag)
                elif kind == "current":
                    username, index, total = args
                    self.current_label.config(text=f"{index}/{total}  {username}")
                elif kind == "progress":
                    done, total = args
                    self.progress["maximum"] = max(total, 1)
                    self.progress["value"] = done
                elif kind == "row":
                    if args and self.history_mode_var.get() == self.engine.options.mode:
                        self._refresh_history()
                elif kind == "started":
                    self._set_controls("running")
                elif kind == "control":
                    self._set_controls(args[0])
                elif kind == "done":
                    counts, aborted, done_count, total, *rest = args
                    reason = rest[0] if rest else ""
                    summary = ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "nothing processed"
                    self._log(f"--- finished: {summary} ({done_count}/{total}) ---", "warn" if aborted else "good")
                    if aborted:
                        self._log(f"Stopped early: {reason}", "error")
                    self.current_label.config(text="")
                    self._set_controls("idle")
                    self._refresh_history()
        except queue.Empty:
            pass
        self.after(120, self._poll_events)

    def _on_close(self) -> None:
        if self.engine.running:
            if not messagebox.askyesno("Still running", "A task is running. Stop it and quit?"):
                return
            self.engine.stop()
            self.engine.join(3)
        self.destroy()


def main() -> None:
    App().mainloop()


if __name__ == "__main__":
    main()
