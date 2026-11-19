"""Web configuration from environment (.env supported). Fail closed in production."""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from pathlib import Path

from ..common import ROOT, env

PLANS = {
    # key: (label, price shown, Stripe price env var, interval, fair-use videos/day, max source minutes)
    "daily": ("Day Pass", "$2", "STRIPE_PRICE_DAILY", "day", 3, 120),
    "monthly": ("Monthly", "$25", "STRIPE_PRICE_MONTHLY", "month", 6, 180),
    "yearly": ("Yearly", "$250", "STRIPE_PRICE_YEARLY", "year", 10, 240),
}


@dataclass
class WebConfig:
    secret_key: str
    base_url: str
    dev: bool
    secure_cookies: bool
    allowed_hosts: list[str]
    data_dir: Path
    stripe_secret: str
    stripe_webhook_secret: str
    max_upload_bytes: int
    workers: int
    google_web_client: str


def load() -> WebConfig:
    dev = env("TT_DEV", "0") == "1"
    key = env("TT_SECRET_KEY")
    if not key:
        if not dev:
            raise RuntimeError("TT_SECRET_KEY must be set in production (python -c \"import secrets;print(secrets.token_urlsafe(48))\")")
        key = secrets.token_urlsafe(48)
    if len(key) < 32:
        raise RuntimeError("TT_SECRET_KEY must be at least 32 characters")
    base = env("TT_BASE_URL", "http://127.0.0.1:8000" if dev else "")
    if not base:
        raise RuntimeError("TT_BASE_URL is required (e.g. https://app.trendtide.example)")
    if not dev and not base.startswith("https://"):
        raise RuntimeError("TT_BASE_URL must be https:// in production")
    hosts = [h.strip() for h in env("TT_ALLOWED_HOSTS", "127.0.0.1,localhost" if dev else "").split(",") if h.strip()]
    if not hosts:
        raise RuntimeError("TT_ALLOWED_HOSTS is required in production")
    return WebConfig(
        secret_key=key, base_url=base.rstrip("/"), dev=dev, secure_cookies=not dev, allowed_hosts=hosts,
        data_dir=Path(env("TT_DATA_DIR", str(ROOT / "data"))), stripe_secret=env("STRIPE_SECRET_KEY"),
        stripe_webhook_secret=env("STRIPE_WEBHOOK_SECRET"),
        max_upload_bytes=int(env("TT_MAX_UPLOAD_MB", "4096")) * 1024 * 1024, workers=int(env("TT_WORKERS", "1")),
        google_web_client=env("GOOGLE_OAUTH_WEB_CLIENT", ".secrets/google_web_client.json"))
