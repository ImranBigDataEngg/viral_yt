"""Auth & hardening: scrypt passwords, hashed server-side sessions, CSRF, login throttling, security headers."""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time

from .db import DB, now

SESSION_COOKIE = "tt_session"
SESSION_TTL = 14 * 24 * 3600
SCRYPT = dict(n=2 ** 14, r=8, p=1, dklen=32)
COMMON = {"password", "password123", "123456789", "qwertyuiop", "letmein123", "iloveyou12", "trendtide1",
          "1234567890", "admin12345", "welcome123"}
EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[A-Za-z]{2,24}$")


def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(pw.encode(), salt=salt, **SCRYPT)
    return f"scrypt${SCRYPT['n']}${SCRYPT['r']}${SCRYPT['p']}${salt.hex()}${dk.hex()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, dk = stored.split("$")
        if algo != "scrypt":
            return False
        cand = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=len(dk) // 2)
        return hmac.compare_digest(cand.hex(), dk)
    except (ValueError, TypeError):
        return False


DUMMY_HASH = hash_password(secrets.token_urlsafe(16))   # equalises timing for unknown emails


def password_problem(pw: str, email: str = "") -> str | None:
    if len(pw) < 10:
        return "Use at least 10 characters."
    if len(pw) > 200:
        return "Password is too long."
    if pw.lower() in COMMON or (email and email.split("@")[0].lower() in pw.lower()):
        return "That password is too easy to guess."
    return None


def normalize_email(email: str) -> str | None:
    e = (email or "").strip().lower()
    return e if EMAIL.match(e) else None


def _h(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(db: DB, user_id: str, ip: str, ua: str) -> str:
    token = secrets.token_urlsafe(32)
    t = now()
    db.run("INSERT INTO sessions VALUES (?,?,?,?,?,?,?)",
           (_h(token), user_id, secrets.token_urlsafe(32), t, t + SESSION_TTL, ip[:64], ua[:200]))
    return token


def get_session(db: DB, token: str | None) -> dict | None:
    if not token or len(token) > 100:
        return None
    s = db.one("SELECT * FROM sessions WHERE token_hash=?", (_h(token),))
    if not s or s["expires"] < now():
        return None
    return s


def destroy_session(db: DB, token: str | None) -> None:
    if token:
        db.run("DELETE FROM sessions WHERE token_hash=?", (_h(token),))


def destroy_all_sessions(db: DB, user_id: str) -> None:
    db.run("DELETE FROM sessions WHERE user_id=?", (user_id,))


def csrf_ok(session: dict | None, supplied: str | None) -> bool:
    return bool(session and supplied and hmac.compare_digest(session["csrf"], supplied))


# ---- login throttling (persistent, survives restarts) -------------------------------------------------------
WINDOW, MAX_FAILS = 15 * 60, 5


def throttled(db: DB, *keys: str) -> bool:
    cutoff = now() - WINDOW
    db.run("DELETE FROM login_failures WHERE at < ?", (cutoff,))
    return any(db.one("SELECT COUNT(*) AS n FROM login_failures WHERE key=? AND at>=?", (k, cutoff))["n"] >= MAX_FAILS
               for k in keys)


def record_failure(db: DB, *keys: str) -> None:
    for k in keys:
        db.run("INSERT INTO login_failures VALUES (?,?)", (k, now()))


def clear_failures(db: DB, *keys: str) -> None:
    for k in keys:
        db.run("DELETE FROM login_failures WHERE key=?", (k,))


# ---- simple in-memory rate limiter for expensive endpoints ---------------------------------------------------
_BUCKETS: dict[str, list[float]] = {}


def rate_limited(key: str, limit: int, per_seconds: int) -> bool:
    t = time.time()
    hits = [h for h in _BUCKETS.get(key, []) if h > t - per_seconds]
    limited = len(hits) >= limit
    if not limited:
        hits.append(t)
    _BUCKETS[key] = hits
    return limited


def security_headers(secure: bool) -> list[tuple[bytes, bytes]]:
    csp = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self' blob:; "
           "connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; "
           "form-action 'self' https://checkout.stripe.com https://billing.stripe.com https://accounts.google.com")
    h = [(b"content-security-policy", csp.encode()), (b"x-content-type-options", b"nosniff"),
         (b"x-frame-options", b"DENY"), (b"referrer-policy", b"strict-origin-when-cross-origin"),
         (b"permissions-policy", b"camera=(), microphone=(), geolocation=(), payment=()"),
         (b"cross-origin-opener-policy", b"same-origin"), (b"cross-origin-resource-policy", b"same-origin")]
    if secure:
        h.append((b"strict-transport-security", b"max-age=63072000; includeSubDomains"))
    return h
