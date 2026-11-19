"""Shared filesystem, settings and input validation helpers."""
from __future__ import annotations

import csv
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def env(name: str, default: str = "") -> str:
    """Read a local .env file without adding a runtime dependency."""
    if name in os.environ:
        return os.environ[name]
    path = ROOT / ".env"
    if not path.exists():
        return default
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == name:
            return value.strip().strip('"').strip("'")
    return default


def safe_name(value: str, maxlen: int = 90) -> str:
    clean = re.sub(r"[^\w.-]+", "_", value, flags=re.UNICODE).strip("._")
    return (clean or "untitled")[:maxlen]


def local_file(value: str) -> Path:
    """Translate configured relative media paths under the workspace root."""
    path = Path(value)
    resolved = (ROOT / path).resolve() if not path.is_absolute() else path.resolve()
    return resolved


def write_csv(path: Path, rows: list[dict], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))
