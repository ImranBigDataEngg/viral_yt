"""Optional: put rendered MP4s in YOUR Google Drive and return real download links for cross-posting
(phone -> TikTok / Instagram). Uses the narrow drive.file scope (only files this app creates).
Links are private to you unless settings.share.anyone_with_link = true."""
from __future__ import annotations

from pathlib import Path

from .common import settings
from .state import record_share, share_for

DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def _drive():
    from googleapiclient.discovery import build
    from .publisher import oauth_credentials
    return build("drive", "v3", credentials=oauth_credentials(DRIVE_SCOPES, "drive_token.json"))


def _folder(drive, name: str) -> str:
    q = f"name='{name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    found = drive.files().list(q=q, fields="files(id)").execute().get("files", [])
    if found:
        return found[0]["id"]
    return drive.files().create(body={"name": name, "mimeType": "application/vnd.google-apps.folder"},
                                fields="id").execute()["id"]


def share_file(output_id: str, file: Path, drive=None) -> str:
    cached = share_for(output_id)
    if cached:
        return cached["link"]
    cfg = settings()["share"]
    if cfg["engine"] != "gdrive":
        raise RuntimeError("Sharing disabled: set share.engine = 'gdrive' in config/settings.json")
    from googleapiclient.http import MediaFileUpload
    drive = drive or _drive()
    folder = _folder(drive, cfg["drive_folder_name"])
    created = drive.files().create(body={"name": file.name, "parents": [folder]}, fields="id,webViewLink,webContentLink",
                                   media_body=MediaFileUpload(str(file), mimetype="video/mp4", resumable=True)).execute()
    if cfg.get("anyone_with_link"):
        drive.permissions().create(fileId=created["id"], body={"type": "anyone", "role": "reader"}).execute()
    link = created.get("webContentLink") or created.get("webViewLink")
    record_share(output_id, "gdrive", link)
    return link
