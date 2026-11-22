# Instagram Follow Manager

A small Linux desktop app (with a CLI too) for cleaning up two things on **your own** Instagram account:

1. **Cancel outgoing follow requests** — requests you sent that are still pending.
2. **Unfollow non-followers** — accounts you follow that don't follow you back.

It drives a Chrome window you are already logged into. It never asks for your password and never stores your credentials.

> **Disclaimer:** browser automation may violate Instagram's Terms of Use and can result in temporary restrictions or an action block. Use it at your own risk, in small batches, with the default delays.

## Features

- **Native Tkinter GUI** — no web server, no browser tab needed for the UI.
- **Start / Pause / Continue / Stop** — the worker thread can be paused and resumed mid-batch, even during the delay between profiles.
- **Dry-run by default** — nothing is clicked until you explicitly disable dry run and confirm.
- **History browser** — see every username that was cancelled, unfollowed, skipped, not found, or left untouched, with search, status filters, summary counts, and CSV export.
- **Flexible inputs for both tasks** — `.txt` (one username per line), Instagram's `pending_follow_requests.json` export, or a simple `.json`/`.csv` list.
- **Resumable** — progress is saved after every profile; re-run to continue where you stopped.
- **Safety guards**
  - Only clicks when the profile button says `Requested` (cancel mode) or `Following` / `Unfollow` (unfollow mode).
  - Never unfollows someone who follows you back (`Follow Back`) or touches an accepted request (`Following` in cancel mode).
  - Detects login walls, security checks, and action blocks and stops immediately.
  - A click whose result can't be verified is recorded as `unverified` and **never retried automatically** (a second click can send a new request / re-follow).
  - Deleted/disabled accounts are recognised quickly and skipped.
- **Configurable** — batch size, min/max delay, Chrome profile path, headless mode, CDP attach, retries.
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
sudo apt install ./dist/instagram-follow-manager_1.0.0_all.deb
```

This creates an **Instagram Follow Manager** entry in your applications menu, plus two commands:

```bash
instagram-follow-manager        # GUI
instagram-follow-manager-cli    # CLI
```

The package installs its Python dependencies via apt. You still need **Google Chrome installed and logged into Instagram**; Selenium downloads a matching ChromeDriver automatically on first launch (needs internet once).

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

1. Choose the task (**Cancel outgoing follow requests** or **Unfollow non-followers**).
2. Pick your input file (`.txt`, `.json` or `.csv`) and click **Load**. Review the preview.
3. Check the settings. Keep **Dry run** on for the first pass.
4. Click **Start**. Use **Pause** / **Continue** / **Stop** as needed.
5. Open the **History** tab to review results and export a CSV.
6. When satisfied, uncheck **Dry run**, confirm, and run a small batch.

If Chrome is already running with your profile, leave **Restart Chrome if in use** enabled (it will close and reopen Chrome with the same profile, so you stay logged in), or launch Chrome yourself with `--remote-debugging-port=9222` and enter `127.0.0.1:9222` in **CDP host:port**.

### CLI

```bash
# Dry run
python cli.py cancel pending_follow_requests.json --limit 10
python cli.py unfollow non_followers.txt --limit 15

# Actually perform the actions
python cli.py cancel pending_follow_requests.json --apply --limit 10
python cli.py unfollow non_followers.txt --apply --limit 15
```

Run `python cli.py --help` for all options.

## Input formats

Both tasks accept the same formats:

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
  state.py                  resumable state store + CSV export
examples/                   anonymised sample inputs
legacy/                     original standalone scripts
```

## Tips

- Instagram rate-limits follow/unfollow actions aggressively. Defaults are 20 per run with 25–45s delays for cancelling, and 15 per run with 45–75s delays for unfollowing. Expect to spread several hundred accounts over multiple days.
- If a run stops with `abort`, that username is not marked done — it will be retried on the next run.
- Use **Retry errors / unavailable** to re-check entries that failed or looked disabled.
- Clear a task's history with **Reset state** in the GUI or `--reset-state` in the CLI.

## License

MIT — see [LICENSE](LICENSE).
