"""SQLite persistence (WAL). Every query is parameterised; every user-owned read is filtered by user_id."""
from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, pw_hash TEXT NOT NULL, created REAL NOT NULL,
  stripe_customer TEXT, accepted_terms REAL NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, csrf TEXT NOT NULL, created REAL NOT NULL, expires REAL NOT NULL,
  ip TEXT, ua TEXT);
CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);
CREATE TABLE IF NOT EXISTS subscriptions (
  user_id TEXT PRIMARY KEY, stripe_subscription TEXT, plan TEXT, status TEXT, current_period_end REAL, updated REAL);
CREATE TABLE IF NOT EXISTS channels (user_id TEXT NOT NULL, channel_id TEXT NOT NULL, title TEXT, verified REAL,
  PRIMARY KEY (user_id, channel_id));
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL, progress TEXT,
  spec TEXT NOT NULL, error TEXT, created REAL NOT NULL, updated REAL NOT NULL, not_before REAL DEFAULT 0);
CREATE INDEX IF NOT EXISTS jobs_user ON jobs(user_id, created);
CREATE INDEX IF NOT EXISTS jobs_queue ON jobs(status, not_before);
CREATE TABLE IF NOT EXISTS outputs (
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, job_id TEXT NOT NULL, title TEXT, ratio TEXT, duration REAL,
  file TEXT NOT NULL, meta_file TEXT NOT NULL, created REAL NOT NULL);
CREATE INDEX IF NOT EXISTS outputs_job ON outputs(user_id, job_id);
CREATE TABLE IF NOT EXISTS stripe_events (id TEXT PRIMARY KEY, type TEXT, received REAL);
CREATE TABLE IF NOT EXISTS login_failures (key TEXT NOT NULL, at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS lf_key ON login_failures(key, at);
"""


class DB:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._local = threading.local()
        self._conn().executescript(SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        con = getattr(self._local, "con", None)
        if con is None:
            con = sqlite3.connect(self.path, timeout=30, isolation_level=None)
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA foreign_keys=ON")
            con.execute("PRAGMA busy_timeout=30000")
            self._local.con = con
        return con

    @contextmanager
    def tx(self):
        con = self._conn()
        con.execute("BEGIN IMMEDIATE")
        try:
            yield con
            con.execute("COMMIT")
        except BaseException:
            con.execute("ROLLBACK")
            raise

    def one(self, sql: str, args=()) -> dict | None:
        row = self._conn().execute(sql, args).fetchone()
        return dict(row) if row else None

    def all(self, sql: str, args=()) -> list[dict]:
        return [dict(r) for r in self._conn().execute(sql, args).fetchall()]

    def run(self, sql: str, args=()) -> int:
        with self.tx() as c:
            return c.execute(sql, args).rowcount


def now() -> float:
    return time.time()
