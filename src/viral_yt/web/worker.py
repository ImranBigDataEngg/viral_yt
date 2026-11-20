"""Background job runner (in-process threads; run `trendtide worker` separately in production)."""
from __future__ import annotations

import json
import shutil
import threading
import time
import traceback
import uuid
from pathlib import Path

from ..common import get_logger
from .db import DB, now

log = get_logger("web.worker")
RETRY_LIVE_S = 600


def user_dir(data_dir: Path, user_id: str) -> Path:
    return data_dir / "users" / user_id


def job_dir(data_dir: Path, user_id: str, job_id: str) -> Path:
    return user_dir(data_dir, user_id) / "jobs" / job_id


def enqueue(db: DB, user_id: str, kind: str, spec: dict) -> str:
    jid = uuid.uuid4().hex
    t = now()
    db.run("INSERT INTO jobs (id,user_id,kind,status,progress,spec,created,updated) VALUES (?,?,?,?,?,?,?,?)",
           (jid, user_id, kind, "queued", "queued", json.dumps(spec), t, t))
    return jid


def claim(db: DB) -> dict | None:
    with db.tx() as c:
        row = c.execute("SELECT * FROM jobs WHERE status IN ('queued','waiting') AND not_before<=? ORDER BY created LIMIT 1",
                        (now(),)).fetchone()
        if not row:
            return None
        c.execute("UPDATE jobs SET status='running', updated=? WHERE id=?", (now(), row["id"]))
        return dict(row)


def _progress(db: DB, jid: str):
    return lambda msg: db.run("UPDATE jobs SET progress=?, updated=? WHERE id=?", (str(msg)[:200], now(), jid))


def process(db: DB, data_dir: Path, job: dict) -> None:
    from ..ingest import IngestError, LiveNotFinished
    from ..social.http import PlatformError
    from ..clipper import ClipError
    from ..tts import TTSError
    from ..transform import TransformError
    spec = json.loads(job["spec"])
    jid, uid = job["id"], job["user_id"]
    say = _progress(db, jid)
    try:
        if job["kind"] == "link":
            from ..linkjob import run_link_job
            channels = {r["channel_id"] for r in db.all("SELECT channel_id FROM channels WHERE user_id=?", (uid,))}
            out_dir = job_dir(data_dir, uid, jid)
            spec["job_id"] = jid
            metas = run_link_job(spec, out_dir, channels, say)
            with db.tx() as c:
                for m in metas:
                    c.execute("INSERT OR REPLACE INTO outputs VALUES (?,?,?,?,?,?,?,?,?)",
                              (m["output_id"], uid, jid, m["title"], m["ratio"], m["duration_seconds"], m["file"],
                               str(Path(m["file"]).with_suffix(".json")), now()))
            db.run("UPDATE jobs SET status='done', progress=?, updated=? WHERE id=?", (f"{len(metas)} clips ready", now(), jid))
        elif job["kind"] == "rerender":
            from ..clipper import rerender
            out = db.one("SELECT * FROM outputs WHERE id=? AND user_id=?", (spec["output_id"], uid))
            if not out:
                raise IngestError("Clip not found")
            meta = json.loads(Path(out["meta_file"]).read_text(encoding="utf-8"))
            if not Path(meta["source_file"]).is_file():
                raise IngestError("The source video was removed after the retention period; re-upload it to edit this clip.")
            say("rendering")
            meta = rerender(out["id"], spec["override"], Path(out["meta_file"]).parent, record=False)
            db.run("UPDATE outputs SET title=?, duration=? WHERE id=? AND user_id=?",
                   (meta["title"], meta["duration_seconds"], out["id"], uid))
            db.run("UPDATE jobs SET status='done', progress='render complete', updated=? WHERE id=?", (now(), jid))
        elif job["kind"] in ("dub", "take"):
            from ..transform import dub, make_original
            out = db.one("SELECT * FROM outputs WHERE id=? AND user_id=?", (spec["output_id"], uid))
            if not out:
                raise IngestError("Clip not found")
            out_dir = Path(out["meta_file"]).parent
            say("translating & voicing" if job["kind"] == "dub" else "recording your take")
            if job["kind"] == "dub":
                meta = dub(out["id"], spec["lang"], out_dir, record=False)
            else:
                vf = Path(spec["voice_file"]) if spec.get("voice_file") else None
                meta = make_original(out["id"], spec.get("take", ""), out_dir, voice_file=vf, record=False)
            db.run("INSERT OR REPLACE INTO outputs VALUES (?,?,?,?,?,?,?,?,?)",
                   (meta["output_id"], uid, out["job_id"], meta["title"], meta["ratio"], meta["duration_seconds"],
                    meta["file"], str(Path(meta["file"]).with_suffix(".json")), now()))
            db.run("UPDATE jobs SET status='done', progress=?, updated=? WHERE id=?",
                   (f"created {meta['output_id']}", now(), jid))
        elif job["kind"] == "publish":
            from .publishing import run_publish
            n_ok, n_err = run_publish(db, uid, jid, spec, say)
            db.run("UPDATE jobs SET status=?, progress=?, updated=? WHERE id=?",
                   ("done" if n_ok else "failed", f"{n_ok} posted, {n_err} failed", now(), jid))
    except LiveNotFinished as exc:
        db.run("UPDATE jobs SET status='waiting', progress=?, not_before=?, updated=? WHERE id=?",
               (str(exc), now() + RETRY_LIVE_S, now(), jid))
    except (IngestError, ValueError, TransformError, PlatformError, ClipError, TTSError) as exc:
        db.run("UPDATE jobs SET status='failed', error=?, progress='failed', updated=? WHERE id=?", (str(exc)[:500], now(), jid))
    except Exception:
        log.error("job %s crashed: %s", jid, traceback.format_exc())
        db.run("UPDATE jobs SET status='failed', error=?, progress='failed', updated=? WHERE id=?",
               ("Processing failed. Please try again or contact support.", now(), jid))
        if job["kind"] == "link":
            shutil.rmtree(job_dir(data_dir, uid, jid) / "_source", ignore_errors=True)


def cleanup_sources(data_dir: Path, retention_days: float) -> int:
    """Delete source videos older than the retention period (clips stay). Returns folders removed."""
    cutoff, n = time.time() - retention_days * 86400, 0
    for src in (data_dir / "users").glob("*/jobs/*/_source"):
        if src.stat().st_mtime < cutoff:
            shutil.rmtree(src, ignore_errors=True)
            n += 1
    for inbox in (data_dir / "users").glob("*/inbox/*"):
        if inbox.stat().st_mtime < time.time() - 86400:
            inbox.unlink(missing_ok=True)
    return n


def loop(db_path: Path, data_dir: Path, stop: threading.Event, poll: float = 2.0) -> None:
    from ..common import env
    db = DB(db_path)
    db.run("UPDATE jobs SET status='queued' WHERE status='running'")   # recover after a crash/restart
    retention, last_clean = float(env("TT_SOURCE_RETENTION_DAYS", "7")), 0.0
    while not stop.is_set():
        if time.time() - last_clean > 3600:
            cleanup_sources(data_dir, retention)
            last_clean = time.time()
        job = claim(db)
        if job:
            process(db, data_dir, job)
        else:
            stop.wait(poll)


def start_threads(db_path: Path, data_dir: Path, n: int) -> threading.Event:
    stop = threading.Event()
    for i in range(max(0, n)):
        threading.Thread(target=loop, args=(db_path, data_dir, stop), name=f"tt-worker-{i}", daemon=True).start()
    return stop
