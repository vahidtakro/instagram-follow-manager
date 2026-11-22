# Instagram Follow Manager

A small Linux desktop app (with a CLI too) for four things on **your own** Instagram account:

1. **Cancel outgoing follow requests** — requests you sent that are still pending.
2. **Unfollow non-followers** — accounts you follow that don't follow you back.
3. **Engage with the feed / reels** — browse like a person and like only a random selection of posts.
4. **Watch stories** — open stories, linger on some, skip others, and move between authors.

It drives a Chrome window you are already logged into. It never asks for your password and never stores your credentials.

> **Disclaimer:** browser automation may violate Instagram's Terms of Use and can result in temporary restrictions or an action block. Use it at your own risk, in small batches, with the default delays.

## Features

- **Human-paced social modes** — variable viewing times, a like probability instead of liking everything, irregular scrolling, small mouse movements, occasional longer pauses, randomly ordered story authors and a story skip probability. Nothing happens on a fixed metronome.
- **Native Tkinter GUI** — no web server, no browser tab needed for the UI.
- **Start / Pause / Continue / Stop** — the worker thread can be paused and resumed mid-batch, even during the delay between profiles.
- **Dry-run by default** — nothing is clicked until you explicitly disable dry run and confirm.
- **History browser** — see every username that was cancelled, unfollowed, skipped, not found, or left untouched, with search, status filters, summary counts, and CSV export.
- **Flexible inputs** — `.txt` (one username per line), Instagram's `pending_follow_requests.json` export, or a simple `.json`/`.csv` list. In Engage mode a loaded file acts as an optional allowlist of authors you're happy to like.
- **Resumable** — progress is saved after every profile; re-run to continue where you stopped.
- **Safety guards**
  - Only clicks when the profile button says `Requested` (cancel mode) or `Following` / `Unfollow` (unfollow mode).
  - Never unfollows someone who follows you back (`Follow Back`) or touches an accepted request (`Following` in cancel mode).
  - Detects login walls, security checks, and action blocks and stops immediately.
  - A click whose result can't be verified is recorded as `unverified` and **never retried automatically** (a second click can send a new request / re-follow).
  - Deleted/disabled accounts are recognised quickly and skipped.
  - Social modes never like the same author twice while that author is in history, and an unverified like is never retried.
- **Configurable** — batch size, random delay range (or a fixed delay), like chance, viewing/story durations, story skip chance, Chrome profile path, ChromeDriver path, headless mode, CDP attach, retries.
- **Works in the terminal too** — same engine, exposed through `cli.py`.

## Screenshots

Run `python app.py` and see it live. The GUI has three tabs: **Run**, **History**, and **About / Safety**.

## Installation

### Recommended: install the Linux desktop app (Debian / Ubuntu)

Download the `.deb` from the [Releases](https://github.com/vahidtakro/instagram-follow-manager/releases) page, or build it from source:

```bash
git clone https://github.com/vahidtakro/instagram-follow-manager.git
cd instagram-follow-manager
bash packaging/build_deb.sh
sudo apt install ./dist/instagram-follow-manager_1.1.2_all.deb
```

This creates an **Instagram Follow Manager** entry in your applications menu, plus two commands:

```bash
instagram-follow-manager        # GUI
instagram-follow-manager-cli    # CLI
```

The package installs its Python dependencies via apt. You still need **Google Chrome installed and logged into Instagram**.

ChromeDriver is located automatically: the app checks an explicit path (the **ChromeDriver path** field in Settings, or `--driver-path` on the CLI), then Selenium's cache, then `chromedriver` on your PATH (provided by the package's `chromium-chromedriver` dependency). If none is found it falls back to Selenium Manager, which needs internet once. On a fresh install the apt dependency supplies a working driver, so no manual setup is needed.

### Run from source (any distro)

```bash
git clone https://github.com/vahidtakro/instagram-follow-manager.git
cd instagram-follow-manager

# Debian/Ubuntu first:
sudo apt install python3-venv python3-tk

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

On Fedora/RHEL use `sudo dnf install python3-tkinter` instead. If `python3 -m venv` complains about missing `ensurepip`, install `python3-venv` (Debian/Ubuntu) or `python3-pip` first, then recreate the folder.

## Usage

### GUI

```bash
python app.py
```

1. Choose one of the four tasks from the **Task** dropdown.
2. For the first two tasks, pick your input file (`.txt`, `.json` or `.csv`) and click **Load**. The social tasks don't need a file — but if you load one in **Engage**, only those authors will be liked.
3. Check the settings. In **Human-paced behaviour** set the like chance (default 35%), how long to look at each post (4–12s), and for stories the skip chance (30%) and watch time (3–9s). Keep **Dry run** on for the first pass.
4. Click **Start**. Use **Pause** / **Continue** / **Stop** as needed.
5. Open the **History** tab to review what was liked, watched or skipped, and export a CSV.
6. When satisfied, uncheck **Dry run**, confirm, and run a small batch.

The **Engage** task visits the home feed first, then (optionally) reels. It spends a variable time on each post, likes only a percentage, scrolls irregularly and moves the mouse occasionally. **Watch stories** picks authors in random order, skips some stories early and watches others for a random duration, then moves to the next person when their stories end.

If Chrome is already running with your profile, leave **Restart Chrome if in use** enabled (it will close and reopen Chrome with the same profile, so you stay logged in), or launch Chrome yourself with `--remote-debugging-port=9222` and enter `127.0.0.1:9222` in **CDP host:port**.

### CLI

```bash
# Dry run
python cli.py cancel pending_follow_requests.json --limit 10
python cli.py unfollow non_followers.txt --limit 15

# Actually perform the actions
python cli.py cancel pending_follow_requests.json --apply --limit 10
python cli.py unfollow non_followers.txt --apply --limit 15

# Human-paced social modes (no input file needed)
python cli.py engage --limit 8                       # feed + reels, dry run
python cli.py engage --limit 8 --like-chance 40 --dwell-min 5 --dwell-max 14
python cli.py engage --limit 8 --apply --no-reels    # feed only
python cli.py stories --limit 10 --skip-chance 25 --story-min 4 --story-max 10
python cli.py engage --watch-stories --limit 12      # stories first, then feed/reels
```

Run `python cli.py --help` for all options.

## Input formats

All tasks that use a file accept the same formats:

**Plain text** — one username per line:

```
# comments and blank lines are ignored
some_user
@another_user
https://www.instagram.com/third_user/
```

**Instagram JSON export** — the format produced by Instagram's "Download your information":

```json
[
  {
    "label_values": [
      { "label": "Username", "value": "some_user" }
    ]
  }
]
```

**Simple JSON / CSV** — a list of strings, `{"usernames": [...]}`, or a CSV with a `username` column also work.

Usernames marked `__deleted__...` in third-party exports are recorded as `not_found` without opening a browser tab.

## Where data is stored

State/history files live in `~/.instagram-follow-manager/`:

- `cancel_state.json`
- `unfollow_state.json`
- `engage_state.json`
- `stories_state.json`

They contain only usernames, statuses and timestamps. They are local and are never uploaded. The `.gitignore` excludes them from this repository — **do not commit files containing real usernames**.

## Project structure

```
app.py                      GUI entry point
cli.py                      CLI entry point
insta_manager/
  browser.py                Chrome setup + atomic JS DOM helpers
  engine.py                 threaded worker (start/pause/resume/stop)
  gui.py                    Tkinter interface
  inputs.py                 txt/json/csv loaders
  social.py                 human-paced feed/reel/story DOM helpers
  state.py                  resumable state store + CSV export
examples/                   anonymised sample inputs
legacy/                     original standalone scripts
```

## Tips

- Instagram rate-limits follow/unfollow actions aggressively. Defaults are 20 per run with 25–45s delays for cancelling, and 15 per run with 45–75s delays for unfollowing. Expect to spread several hundred accounts over multiple days.
- **Random delay (jitter) is on by default.** Each wait is drawn randomly between "Delay from" and "to". If you enter only one number (or set both equal), the app still adds ±30% jitter so the timing never looks mechanical. Uncheck **Random delay** (or pass `--fixed-delay`) only if you deliberately want an exact wait.
- **The batch size is editable.** The **Max items this run** field (default 50 for social tasks, 20/15 for the cleanup tasks) is a per-run target, not a hard cap — set it to 100, 200 or more if you want. In the CLI use `--limit N`. Likes are still rate-limited: keep **Like chance** modest (20–40%) and grow batches gradually.
- Instagram swaps the story tray as stories are consumed. The watcher re-reads it when the viewer closes, so it can keep going past one person's stories instead of stopping early.
- The social modes store one entry per author, so an author is never engaged twice until you reset that task's state. That also means re-running a social task mostly does nothing until you reset or switch task.
- **A large limit is a target, not a guarantee.** The app stops when Instagram has no new posts/stories/reels left for that session, and it now says so in the log (and reloads the feed a few times first). At default viewing times, 500 items is already several hours — work in sessions of 20–100 and wait for fresh content between them.
- If a run stops with `abort`, that username is not marked done — it will be retried on the next run.
- Use **Retry errors / unavailable** to re-check entries that failed or looked disabled.
- Clear a task's history with **Reset state** in the GUI or `--reset-state` in the CLI.

## License

MIT — see [LICENSE](LICENSE).
