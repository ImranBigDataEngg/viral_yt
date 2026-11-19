"""Gated OAuth upload of rendered Shorts to YOUR channel (YouTube Data API videos.insert).

Defaults: dry-run, private. Public needs --publish AND fresh approval (ledger publish_approved / brief
publish_approved) AND originality score >= threshold AND under the daily cap.
NOTE: uploads from an API project that has not passed YouTube's API compliance audit are locked
private by YouTube. Apply for the audit before expecting --publish to work.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .common import ROOT, env, is_true, safe_name, settings
from .rights import load_asset, validate_asset
from .state import record_upload, upload_for, uploads_today

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
OUT = ROOT / "output" / "shorts"


def oauth_credentials(scopes: list[str], token_name: str):
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:
        raise RuntimeError("Install upload extras: pip install -r requirements.txt") from exc
    secrets = ROOT / env("YOUTUBE_CLIENT_SECRET", ".secrets/client_secret.json")
    token = ROOT / ".secrets" / token_name
    token.parent.mkdir(parents=True, exist_ok=True)
    creds = Credentials.from_authorized_user_file(str(token), scopes=scopes) if token.exists() else None
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not secrets.exists():
                raise RuntimeError(f"No OAuth desktop client JSON at {secrets}. Create it in Google Cloud Console.")
            creds = InstalledAppFlow.from_client_secrets_file(str(secrets), scopes).run_local_server(port=0)
        token.write_text(creds.to_json(), encoding="utf-8")
    return creds


def get_youtube_client():
    from googleapiclient.discovery import build
    name = Path(env("YOUTUBE_OAUTH_TOKEN", ".secrets/token.json")).name
    return build("youtube", "v3", credentials=oauth_credentials(SCOPES, name))


def load_meta(output_id: str) -> dict:
    side = OUT / f"{safe_name(output_id)}.json"
    if not side.is_file():
        raise RuntimeError(f"No render sidecar {side}. Render first.")
    return json.loads(side.read_text(encoding="utf-8"))


def fresh_publish_approval(meta: dict) -> bool:
    if meta.get("kind") == "explainer":
        brief = json.loads(Path(meta["brief_file"]).read_text(encoding="utf-8"))
        return is_true(brief.get("publish_approved"))
    asset = load_asset(meta["asset_id"])
    validate_asset(asset, require_publish=True)
    return True


def build_body(meta: dict, privacy: str, publish_at: str | None) -> dict:
    cfg = settings()
    desc = (meta.get("description") or "").strip()
    if meta.get("kind") == "clip" and meta.get("rights_basis") != "original":
        desc += f"\n\nFootage: {meta.get('source_owner')} — used under {meta.get('rights_basis')}"
        if meta.get("source_video_url"):
            desc += f" ({meta['source_video_url']})"
    tags = [t for t in meta.get("tags", []) if t][:15]
    hashtags = " ".join((meta.get("hashtags") or cfg["brand"]["hashtags"])[:3])
    desc = f"{desc}\n\n{hashtags}".strip()
    status = {"privacyStatus": privacy, "selfDeclaredMadeForKids": bool(cfg["publishing"]["made_for_kids"]),
              "containsSyntheticMedia": bool(meta.get("contains_realistic_synthetic_media"))}
    if publish_at:
        datetime.fromisoformat(publish_at.replace("Z", "+00:00"))  # validate
        status.update(privacyStatus="private", publishAt=publish_at)
    return {"snippet": {"title": (meta.get("title") or "Untitled")[:100], "description": desc[:4900], "tags": tags,
                        "categoryId": str(cfg["publishing"]["category_id"])}, "status": status}


def upload(output_id: str, execute: bool = False, publish: bool = False, publish_at: str | None = None,
           client=None) -> dict:
    cfg = settings()["publishing"]
    meta = load_meta(output_id)
    video = Path(meta["file"])
    if not video.is_file():
        raise RuntimeError(f"Missing rendered file: {video}")
    if meta.get("kind") == "clip":
        validate_asset(load_asset(meta["asset_id"]))  # rights still valid now?
    going_public = publish or bool(publish_at)
    if going_public:
        if not fresh_publish_approval(meta):
            raise RuntimeError("publish_approved is not true for this item")
        if int(meta.get("originality_score", 0)) < int(cfg["min_originality_score"]):
            raise RuntimeError(f"Originality score {meta.get('originality_score')} < {cfg['min_originality_score']}: "
                               f"add your own voice-over/insight first. Reasons: {meta.get('originality_reasons')}")
    privacy = "public" if publish else cfg["default_privacy"]
    body = build_body(meta, privacy, publish_at)
    done = upload_for(meta["output_id"])
    response = {"mode": "upload" if execute else "dry_run", "output_id": meta["output_id"], "file": str(video),
                "body": body, "already_uploaded": done}
    if not execute or done:
        return response
    if uploads_today() >= int(cfg["daily_upload_cap"]):
        raise RuntimeError(f"Daily upload cap {cfg['daily_upload_cap']} reached (protects against 'mass-produced' signals)")
    from googleapiclient.http import MediaFileUpload
    client = client or get_youtube_client()
    request = client.videos().insert(part="snippet,status", body=body, notifySubscribers=privacy == "public",
                                     media_body=MediaFileUpload(str(video), mimetype="video/mp4", resumable=True))
    result = None
    while result is None:
        _, result = request.next_chunk()
    vid = result.get("id")
    if not vid:
        raise RuntimeError(f"YouTube API did not return a video ID: {result}")
    url = f"https://www.youtube.com/shorts/{vid}"
    record_upload(meta["output_id"], vid, url, body["status"]["privacyStatus"])
    response.update(youtube_url=url, video_id=vid, privacy=body["status"]["privacyStatus"])
    video.with_suffix(".youtube.json").write_text(json.dumps(response, indent=2), encoding="utf-8")
    return response
