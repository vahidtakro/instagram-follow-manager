"""Input loaders: plain text and Instagram JSON exports, for both workflows."""

from __future__ import annotations

import json
import re
from pathlib import Path

USERNAME_RE = re.compile(r"^[A-Za-z0-9._]{1,30}$")


def _clean(line: str) -> str | None:
    value = line.strip()
    if not value or value.startswith("#"):
        return None
    # Accept @name, full profile URLs, and plain usernames.
    if value.startswith("@"):
        value = value[1:]
    if "instagram.com/" in value:
        value = value.split("instagram.com/", 1)[1].strip("/").split("/")[0].split("?")[0]
    value = value.strip().rstrip("/")
    if not value or not USERNAME_RE.match(value):
        return None
    return value


def _from_json(data) -> list[str]:
    names: list[str] = []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, str):
                names.append(item)
            elif isinstance(item, dict):
                if "label_values" in item:
                    for lv in item["label_values"]:
                        if lv.get("label") == "Username" and lv.get("value"):
                            names.append(lv["value"])
                for key in ("username", "username_value", "value", "name"):
                    if item.get(key):
                        names.append(str(item[key]))
                        break
    elif isinstance(data, dict):
        for key in ("usernames", "users", "data", "following", "followers"):
            if key in data:
                names.extend(_from_json(data[key]))
        if "label_values" in data:
            names.extend(_from_json([data]))
    return names


def load_usernames(path: str | Path) -> tuple[list[str], list[str]]:
    """Return (usernames, warnings). Supports .txt, .json, and .csv files."""
    path = Path(path).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {path}")
    raw = path.read_text(encoding="utf-8", errors="replace")

    names: list[str] = []
    warnings: list[str] = []
    if path.suffix.lower() == ".json" or raw.lstrip()[:1] in {"[", "{"}:
        try:
            names = _from_json(json.loads(raw))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON in {path}: {exc}") from exc
    else:
        for line in raw.splitlines():
            cleaned = _clean(line)
            if cleaned:
                names.append(cleaned)
            elif line.strip() and not line.strip().startswith("#"):
                warnings.append(f"Skipped unrecognised line: {line.strip()[:60]}")

    unique = list(dict.fromkeys(n for n in (n.strip() for n in names) if n))
    if not unique:
        warnings.append("No usernames were found in the file.")
    return unique, warnings
