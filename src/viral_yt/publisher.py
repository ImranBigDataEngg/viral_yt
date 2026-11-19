"""Explicitly gated OAuth upload to the operator's own YouTube channel."""
from __future__ import annotations

import json
from pathlib import Path
from .common import ROOT, env, safe_name
from .rights import load_asset, validate_asset

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def get_youtube_client():
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise RuntimeError("Install upload extras: pip install -r requirements.txt") from exc
    secrets = ROOT / env("YOUTUBE_CLIENT_SECRET", ".secrets/client_secret.json")
    token = ROOT / env("YOUTUBE_OAUTH_TOKEN", ".secrets/token.json")
    token.parent.mkdir(parents=True, exist_ok=True)
    creds = None
    if token.exists():
        creds = Credentials.from_authorized_user_file(str(token), scopes=SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not secrets.exists():
                raise RuntimeError(f"No OAuth desktop client JSON: {secrets}. Create credentials in Google Cloud and place them here.")
            flow = InstalledAppFlow.from_client_secrets_file(str(secrets), SCOPES)
            creds = flow.run_local_server(port=0)
        token.write_text(creds.to_json(), encoding="utf-8")
    return build("youtube", "v3", credentials=creds)


def upload(asset_id: str, execute: bool = False, publish: bool = False) -> dict:
    asset = load_asset(asset_id)
    validate_asset(asset, require_publish=publish)
    video = ROOT / "output" / "shorts" / f"{safe_name(asset_id)}.mp4"
    if not video.is_file():
        raise RuntimeError(f"Missing rendered file. Run render first: {video}")
    title = (asset.get("title") or "Untitled original Short")[:100]
    desc = (asset.get("description") or "").strip()
    if asset.get("source_video_url"):
        desc += f"\n\nPermitted source: {asset['source_video_url']}"
    if asset.get("owner"):
        desc += f"\nFootage credit: {asset['owner']}"
    desc += "\n\n#Shorts #Trends"
    privacy = "public" if publish else "private"
    response = {"mode": "dry_run" if not execute else "upload", "privacy": privacy, "asset_id": asset_id, "file": str(video), "title": title}
    if not execute:
        return response
    from googleapiclient.http import MediaFileUpload
    client = get_youtube_client()
    request = client.videos().insert(part="snippet,status", body={
        "snippet": {"title": title, "description": desc[:4800], "categoryId": "24"},
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False},
    }, media_body=MediaFileUpload(str(video), mimetype="video/mp4", resumable=True))
    result = None
    while result is None:
        _, result = request.next_chunk()
    video_id = result.get("id")
    if not video_id:
        raise RuntimeError(f"YouTube API did not return a video ID: {result}")
    response.update(youtube_url=f"https://www.youtube.com/watch?v={video_id}", video_id=video_id)
    video.with_suffix(".youtube.json").write_text(json.dumps(response, indent=2), encoding="utf-8")
    return response
