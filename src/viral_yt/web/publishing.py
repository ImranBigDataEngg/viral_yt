"""Connect platform accounts (OAuth) and publish selected clips. Tokens are encrypted at rest.

Gates before anything is posted:
  * the user explicitly selected clips + accounts and ticked the consent box (TikTok's guidelines require it)
  * the account was connected by this user via the platform's own OAuth (a typed handle never suffices)
  * PUBLIC posts need the clip's originality score >= publishing.min_originality_score
  * per-user cap of posts per platform per 24 h (anti-spam; also protects the accounts)
"""
from __future__ import annotations

import json
import secrets
import time
import uuid
from pathlib import Path

from ..common import env, get_logger, settings
from ..social.http import PlatformError
from ..social.platforms import PLATFORMS
from ..social.tokens import TokenVault
from .db import DB, now

log = get_logger("web.publishing")
DAILY_CAP = int(env("TT_POSTS_PER_PLATFORM_DAY", "10"))
PUBLIC_LEVELS = {"youtube": {"public"}, "tiktok": {"PUBLIC_TO_EVERYONE"}, "instagram": {"public"}}
_registry = dict(PLATFORMS)           # tests may swap in fakes


def adapter(platform: str):
    if platform not in _registry:
        raise PlatformError(f"Unknown platform {platform}")
    return _registry[platform]()


def vault() -> TokenVault:
    from .config import load
    return TokenVault(load().secret_key)


def save_account(db: DB, user_id: str, platform: str, acct: dict) -> str:
    existing = db.one("SELECT id FROM social_accounts WHERE user_id=? AND platform=? AND account_id=?",
                      (user_id, platform, acct["account_id"]))
    aid = existing["id"] if existing else uuid.uuid4().hex[:16]
    db.run("INSERT OR REPLACE INTO social_accounts VALUES (?,?,?,?,?,?,?)",
           (aid, user_id, platform, acct["account_id"], acct.get("handle", "")[:120], vault().seal(acct["token"]), now()))
    if platform == "youtube":   # connecting YouTube with upload rights also proves channel ownership
        db.run("INSERT OR REPLACE INTO channels VALUES (?,?,?,?)", (user_id, acct["account_id"], acct.get("handle", ""), now()))
    return aid


def accounts(db: DB, user_id: str) -> list[dict]:
    return db.all("SELECT id, platform, account_id, handle, created FROM social_accounts WHERE user_id=? ORDER BY platform",
                  (user_id,))


def validate_request(db: DB, user_id: str, body: dict) -> dict:
    ids = [str(x) for x in body.get("output_ids", [])][:20]
    targets = body.get("targets", [])[:6]
    if not ids or not targets:
        raise ValueError("Select at least one clip and one destination")
    if body.get("consent") is not True:
        raise ValueError("Please confirm you want these clips published to the selected accounts")
    outs = []
    for oid in ids:
        o = db.one("SELECT * FROM outputs WHERE id=? AND user_id=?", (oid, user_id))
        if not o:
            raise ValueError("Clip not found")
        outs.append(o)
    clean = []
    for t in targets:
        acc = db.one("SELECT * FROM social_accounts WHERE id=? AND user_id=?", (str(t.get("account")), user_id))
        if not acc:
            raise ValueError("Connect the destination account first (Account → Connected platforms)")
        privacy = str(t.get("privacy") or "")
        if acc["platform"] == "youtube" and privacy not in ("private", "unlisted", "public"):
            raise ValueError("Choose a YouTube visibility")
        if acc["platform"] == "tiktok" and privacy not in ("SELF_ONLY", "MUTUAL_FOLLOW_FRIENDS", "FOLLOWER_OF_CREATOR",
                                                          "PUBLIC_TO_EVERYONE"):
            raise ValueError("Choose who can view the TikTok post")
        if acc["platform"] == "instagram":
            privacy = "public"                       # Instagram Reels from a public professional account are public
        used = db.one("SELECT COUNT(*) AS n FROM posts WHERE user_id=? AND platform=? AND created>?",
                      (user_id, acc["platform"], now() - 86400))["n"]
        if used + len(outs) > DAILY_CAP:
            raise ValueError(f"Daily limit: {DAILY_CAP} {acc['platform']} posts per 24 h")
        if privacy in PUBLIC_LEVELS.get(acc["platform"], set()):
            floor = int(settings()["publishing"]["min_originality_score"])
            for o in outs:
                meta = json.loads(Path(o["meta_file"]).read_text(encoding="utf-8"))
                if int(meta.get("originality_score", 0)) < floor:
                    raise ValueError(f"'{o['title']}' scores {meta.get('originality_score')} on originality (needs {floor} "
                                     "to post publicly). Use 'Make it original' to add your take first.")
        clean.append({"account": acc["id"], "platform": acc["platform"], "privacy": privacy})
    caption = str(body.get("caption") or "")[:2200]
    return {"output_ids": [o["id"] for o in outs], "targets": clean, "caption": caption}


def run_publish(db: DB, user_id: str, job_id: str, spec: dict, say) -> tuple[int, int]:
    ok = err = 0
    for oid in spec["output_ids"]:
        out = db.one("SELECT * FROM outputs WHERE id=? AND user_id=?", (oid, user_id))
        if not out:
            continue
        meta = json.loads(Path(out["meta_file"]).read_text(encoding="utf-8"))
        tags = [t for t in meta.get("tags", []) if t]
        caption = spec.get("caption") or meta.get("title") or ""
        for t in spec["targets"]:
            acc = db.one("SELECT * FROM social_accounts WHERE id=? AND user_id=?", (t["account"], user_id))
            pid = uuid.uuid4().hex[:16]
            if not acc:
                continue
            say(f"posting {out['title'][:40]} to {acc['platform']}")
            try:
                ad = adapter(acc["platform"])
                tok = vault().open(acc["token_enc"])
                tok = ad.refresh(tok)
                db.run("UPDATE social_accounts SET token_enc=? WHERE id=?", (vault().seal(tok), acc["id"]))
                res = ad.publish(tok, acc["account_id"], Path(out["file"]), {
                    "title": out["title"] or caption[:90], "description": caption + (
                        f"\n\nFootage: {meta.get('source_owner')}" if meta.get("rights_basis") not in (None, "original") else ""),
                    "caption": caption, "tags": tags, "privacy": t["privacy"], "tiktok_privacy": t["privacy"],
                    "duration": meta.get("duration_seconds", 30),
                    "synthetic": bool(meta.get("contains_realistic_synthetic_media"))})
                db.run("INSERT INTO posts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                       (pid, user_id, oid, acc["platform"], acc["account_id"], acc["handle"], res.get("status", "posted"),
                        res.get("remote_id"), res.get("url"), None, now(), job_id))
                ok += 1
            except (PlatformError, ValueError, KeyError) as exc:
                log.warning("publish %s -> %s failed: %s", oid, acc["platform"], exc)
                db.run("INSERT INTO posts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                       (pid, user_id, oid, acc["platform"], acc["account_id"], acc["handle"], "failed", None, None,
                        str(exc)[:400], now(), job_id))
                err += 1
    return ok, err


def oauth_start(platform: str, base_url: str) -> tuple[str, dict]:
    ad = adapter(platform)
    if not ad.configured():
        raise PlatformError(f"{ad.label} posting isn't configured on this server (see docs/WEB_APP.md → Social posting)")
    state, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(64)
    url = ad.auth_url(state, f"{base_url}/social/callback/{platform}", verifier)
    return url, {"s": state, "v": verifier, "p": platform, "t": time.time()}
