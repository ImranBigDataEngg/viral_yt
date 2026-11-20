"""Explicitly gated OAuth upload to the operator's own YouTube channel."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from .common import ROOT, env, safe_name
from .rights import is_true, load_asset, validate_asset
from .series import build_description, build_title, get_series

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
# Posting more than this per day looks mass-produced; the playbook targets 1-2.
DAILY_UPLOAD_CAP = int(env("DAILY_UPLOAD_CAP", "2"))
UPLOAD_LOG = ROOT / "output" / "uploads" / "upload_log.jsonl"


def uploads_today(log: Path = UPLOAD_LOG) -> int:
    if not log.is_file():
        return 0
    today = datetime.now(timezone.utc).date().isoformat()
    return sum(1 for line in log.read_text(encoding="utf-8").splitlines() if line.strip() and json.loads(line).get("date") == today)


def get_youtube_client(scopes: list[str] | None = None, token_name: str | None = None):
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise RuntimeError("Install upload extras: pip install -r requirements.txt") from exc
    secrets = ROOT / env("YOUTUBE_CLIENT_SECRET", ".secrets/client_secret.json")
    scopes = scopes or SCOPES
    token = ROOT / (token_name or env("YOUTUBE_OAUTH_TOKEN", ".secrets/token.json"))
    token.parent.mkdir(parents=True, exist_ok=True)
    creds = None
    if token.exists():
        creds = Credentials.from_authorized_user_file(str(token), scopes=scopes)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not secrets.exists():
                raise RuntimeError(f"No OAuth desktop client JSON: {secrets}. Create credentials in Google Cloud and place them here.")
            flow = InstalledAppFlow.from_client_secrets_file(str(secrets), scopes)
            creds = flow.run_local_server(port=0)
        token.write_text(creds.to_json(), encoding="utf-8")
    return build("youtube", "v3", credentials=creds)


def upload(asset_id: str, execute: bool = False, publish: bool = False) -> dict:
    asset = load_asset(asset_id)
    validate_asset(asset, require_publish=publish)
    video = ROOT / "output" / "shorts" / f"{safe_name(asset_id)}.mp4"
    if not video.is_file():
        raise RuntimeError(f"Missing rendered file. Run render first: {video}")
    series_key = (asset.get("series") or "").strip()
    warnings: list[str] = []
    if series_key:
        title, warnings = build_title(series_key, asset.get("subject") or asset.get("title", ""), asset.get("title", ""))
        desc = build_description(asset)
        category_id = get_series(series_key)["category_id"]
    else:
        title = (asset.get("title") or "Untitled original Short")[:100]
        desc = build_description(asset)
        category_id = "24"
    synthetic = is_true(asset.get("synthetic_media"))
    if execute and uploads_today() >= DAILY_UPLOAD_CAP:
        raise RuntimeError(f"Daily upload cap reached ({DAILY_UPLOAD_CAP}). Schedule the rest for tomorrow.")
    privacy = "public" if publish else "private"
    response = {"mode": "dry_run" if not execute else "upload", "privacy": privacy, "asset_id": asset_id, "file": str(video), "title": title, "description": desc, "warnings": warnings, "synthetic_media": synthetic}
    if not execute:
        return response
    from googleapiclient.http import MediaFileUpload
    client = get_youtube_client()
    request = client.videos().insert(part="snippet,status", body={
        "snippet": {"title": title, "description": desc[:4800], "categoryId": category_id,
                    "defaultLanguage": "en", "defaultAudioLanguage": "en"},
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False,
                   "containsSyntheticMedia": synthetic},
    }, media_body=MediaFileUpload(str(video), mimetype="video/mp4", resumable=True))
    result = None
    while result is None:
        _, result = request.next_chunk()
    video_id = result.get("id")
    if not video_id:
        raise RuntimeError(f"YouTube API did not return a video ID: {result}")
    response.update(youtube_url=f"https://www.youtube.com/watch?v={video_id}", video_id=video_id)
    video.with_suffix(".youtube.json").write_text(json.dumps(response, indent=2), encoding="utf-8")
    UPLOAD_LOG.parent.mkdir(parents=True, exist_ok=True)
    with UPLOAD_LOG.open("a", encoding="utf-8") as log:
        log.write(json.dumps({"date": datetime.now(timezone.utc).date().isoformat(), "asset_id": asset_id, "video_id": video_id, "series": series_key}) + "\n")
    return response
