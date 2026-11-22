"""Resumable JSON state store with CSV export."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

# Dry-run results and aborts must never count as finished work.
UNHANDLED = {"would_cancel", "would_unfollow", "would_like", "would_view", "abort"}
RETRYABLE = {"error", "unavailable"}

STATUS_LABELS = {
    "cancelled": "Cancelled (outgoing request)",
    "unfollowed": "Unfollowed",
    "would_cancel": "Pending - dry run",
    "would_unfollow": "Pending - dry run",
    "would_like": "Would like - dry run",
    "would_view": "Would watch - dry run",
    "liked": "Liked",
    "viewed": "Viewed (left unliked / watched story)",
    "following": "Request accepted - untouched",
    "follow": "No pending request - untouched",
    "follow_back": "Follows you - untouched",
    "requested": "Still pending - untouched",
    "not_found": "Profile not found / deleted",
    "unavailable": "Disabled or blank profile",
    "unverified": "Click result unknown - check manually",
    "abort": "Stopped for safety",
    "error": "Error",
    "skipped": "Skipped",
}


class StateStore:
    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()
        self.items: dict[str, dict] = {}
        self.load()

    def load(self) -> None:
        if self.path.exists():
            try:
                self.items = json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self.items = {}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self.items, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    def reset(self) -> None:
        self.items = {}
        if self.path.exists():
            self.path.unlink()

    def handled(self, username: str, retry_errors: bool = False) -> bool:
        entry = self.items.get(username)
        if not entry:
            return False
        status = entry.get("status")
        if status in UNHANDLED:
            return False
        if retry_errors and status in RETRYABLE:
            return False
        return True

    def record(self, username: str, status: str, detail: str = "") -> dict:
        entry = {
            "status": status,
            "detail": detail,
            "time": datetime.now(timezone.utc).isoformat(),
        }
        self.items[username] = entry
        self.save()
        return entry

    def rows(self) -> list[dict]:
        rows = []
        for username, entry in self.items.items():
            rows.append({
                "username": username,
                "status": entry.get("status", ""),
                "label": STATUS_LABELS.get(entry.get("status", ""), entry.get("status", "")),
                "detail": entry.get("detail", ""),
                "time": entry.get("time", ""),
            })
        return sorted(rows, key=lambda row: row["time"], reverse=True)

    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for entry in self.items.values():
            status = entry.get("status", "unknown")
            counts[status] = counts.get(status, 0) + 1
        return counts

    def export_csv(self, path: str | Path, rows: list[dict] | None = None) -> None:
        path = Path(path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(["username", "status", "description", "detail", "time"])
            for row in rows if rows is not None else self.rows():
                writer.writerow([row["username"], row["status"], row["label"], row["detail"], row["time"]])
