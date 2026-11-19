"""SQLite run history: dedupe across runs, idempotent renders/uploads, daily caps."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .common import ROOT

SCHEMA = """
CREATE TABLE IF NOT EXISTS candidates (
  video_id TEXT PRIMARY KEY, category TEXT, title TEXT, channel TEXT, lane TEXT, license TEXT,
  first_seen TEXT, last_seen TEXT, best_score REAL, times_seen INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS renders (
  output_id TEXT PRIMARY KEY, asset_id TEXT, kind TEXT, file TEXT, title TEXT,
  originality_score INTEGER, rendered_utc TEXT, meta TEXT);
CREATE TABLE IF NOT EXISTS uploads (
  output_id TEXT PRIMARY KEY, video_id TEXT, url TEXT, privacy TEXT, uploaded_utc TEXT);
CREATE TABLE IF NOT EXISTS shares (
  output_id TEXT PRIMARY KEY, engine TEXT, link TEXT, shared_utc TEXT);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def db_path() -> Path:
    path = ROOT / "output" / "state.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


@contextmanager
def connect(path: Path | None = None):
    con = sqlite3.connect(path or db_path())
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    try:
        yield con
        con.commit()
    finally:
        con.close()


def remember_candidates(rows: list[dict], path: Path | None = None) -> None:
    """Annotate rows with is_new/times_seen and persist them."""
    stamp = now()
    with connect(path) as con:
        for row in rows:
            old = con.execute("SELECT times_seen, best_score FROM candidates WHERE video_id=?", (row["video_id"],)).fetchone()
            row["is_new"] = old is None
            row["times_seen"] = 1 if old is None else old["times_seen"] + 1
            if old is None:
                con.execute("INSERT INTO candidates VALUES (?,?,?,?,?,?,?,?,?,1)", (
                    row["video_id"], row.get("category"), row.get("title"), row.get("channel"), row.get("lane"),
                    row.get("license"), stamp, stamp, row.get("score", 0)))
            else:
                con.execute("UPDATE candidates SET last_seen=?, times_seen=times_seen+1, best_score=MAX(best_score, ?) WHERE video_id=?",
                            (stamp, row.get("score", 0), row["video_id"]))


def record_render(output_id: str, asset_id: str, kind: str, file: str, title: str, score: int, meta: dict) -> None:
    with connect() as con:
        con.execute("INSERT OR REPLACE INTO renders VALUES (?,?,?,?,?,?,?,?)",
                    (output_id, asset_id, kind, file, title, score, now(), json.dumps(meta)))


def record_upload(output_id: str, video_id: str, url: str, privacy: str) -> None:
    with connect() as con:
        con.execute("INSERT OR REPLACE INTO uploads VALUES (?,?,?,?,?)", (output_id, video_id, url, privacy, now()))


def record_share(output_id: str, engine: str, link: str) -> None:
    with connect() as con:
        con.execute("INSERT OR REPLACE INTO shares VALUES (?,?,?,?)", (output_id, engine, link, now()))


def upload_for(output_id: str) -> dict | None:
    with connect() as con:
        row = con.execute("SELECT * FROM uploads WHERE output_id=?", (output_id,)).fetchone()
        return dict(row) if row else None


def share_for(output_id: str) -> dict | None:
    with connect() as con:
        row = con.execute("SELECT * FROM shares WHERE output_id=?", (output_id,)).fetchone()
        return dict(row) if row else None


def uploads_today() -> int:
    day = datetime.now(timezone.utc).date().isoformat()
    with connect() as con:
        return con.execute("SELECT COUNT(*) FROM uploads WHERE uploaded_utc LIKE ?", (day + "%",)).fetchone()[0]


def published_titles() -> list[str]:
    with connect() as con:
        return [r[0] for r in con.execute("SELECT r.title FROM renders r JOIN uploads u USING(output_id)")]
