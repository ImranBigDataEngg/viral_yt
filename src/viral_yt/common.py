"""Shared filesystem, settings, logging and input validation helpers."""
from __future__ import annotations

import csv
import json
import logging
import os
import re
from copy import deepcopy
from pathlib import Path

ROOT = Path(os.environ.get("TRENDTIDE_ROOT") or Path(__file__).resolve().parents[2])

DEFAULT_SETTINGS: dict = {
    "brand": {"name": "TrendTide", "handle": "@TrendTide", "tagline": "The story behind what's trending.",
               "hashtags": ["#Shorts", "#Trending"]},
    "discovery": {"region": "CA", "window_days": 7, "top": 10, "lanes": ["search", "chart", "cc"],
                   "chart_category_ids": {"Gaming": "20", "Entertainment": "24", "AI & Tech": "28"},
                   "outlier_lookup": True},
    "render": {"width": 1080, "height": 1920, "fps": 30, "crf": 21, "preset": "medium",
                "layout": "blurfit", "loudness_lufs": -14, "font": "Arial", "caption_size": 78,
                "hook_seconds": 2.6, "max_words_per_caption": 3},
    "clips": {"min_seconds": 20, "max_seconds": 58, "per_source": 3},
    "voice": {"engine": "auto", "piper_model": "models/piper/en_US-ryan-high.onnx", "flite_voice": "slt"},
    "llm": {"engine": "none", "ollama_url": "http://127.0.0.1:11434", "ollama_model": "llama3.1:8b"},
    "publishing": {"daily_upload_cap": 3, "min_originality_score": 70, "default_privacy": "private",
                    "category_id": "24", "made_for_kids": False},
    "share": {"engine": "none", "drive_folder_name": "TrendTide Exports", "anyone_with_link": False},
}


def _merge(base: dict, extra: dict) -> dict:
    out = deepcopy(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def settings() -> dict:
    path = ROOT / "config" / "settings.json"
    if path.exists():
        return _merge(DEFAULT_SETTINGS, json.loads(path.read_text(encoding="utf-8")))
    return deepcopy(DEFAULT_SETTINGS)


def get_logger(name: str = "trendtide") -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        logs = ROOT / "output" / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(logs / "trendtide.log", encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


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
    return (ROOT / path).resolve() if not path.is_absolute() else path.resolve()


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


def is_true(value) -> bool:
    return value is True or str(value).strip().lower() in ("true", "yes", "1", "y")
