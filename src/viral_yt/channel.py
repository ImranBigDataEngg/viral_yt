"""One-time channel SEO setup for EzzyClips Ultra: description, keywords, playlists.

Dry-run by default. With --execute it uses OAuth with the full `youtube` scope
(stored in a separate token from the upload-only token).
"""
from __future__ import annotations

from .common import env
from .series import SERIES

MANAGE_SCOPES = ["https://www.googleapis.com/auth/youtube"]

CHANNEL_DESCRIPTION = """Viral clips explained in under 60 seconds. EzzyClips Ultra breaks down the internet's most-replayed moments: the editing trick, the camera illusion, the meme's real origin, frame by frame.

New breakdown every day. Watch the explanation, then replay the clip and catch what you missed.

Series: How Was This Filmed? | Meme Origins | You Missed This | Real or Fake? | Trend in 30s"""

CHANNEL_KEYWORDS = [
    "viral clips explained", "how was this filmed", "meme origins", "video editing tricks",
    "camera tricks", "optical illusions explained", "viral moments breakdown",
    "internet trends explained", "real or fake", "behind the clip", "EzzyClips Ultra",
]


def keywords_string(keywords: list[str] = CHANNEL_KEYWORDS) -> str:
    """YouTube stores channel keywords space-separated; multi-word ones must be quoted."""
    value = " ".join(f'"{k}"' if " " in k else k for k in keywords)
    if len(value) > 500:
        raise ValueError(f"Channel keywords are {len(value)} chars; the limit is 500")
    return value


def planned_changes(business_email: str = "") -> dict:
    description = CHANNEL_DESCRIPTION + (f"\n\nBusiness: {business_email}" if business_email else "")
    return {
        "brandingSettings": {"channel": {
            "description": description,
            "keywords": keywords_string(),
            "country": env("YOUTUBE_REGION", "CA"),
            "defaultLanguage": "en",
        }},
        "playlists": [
            {"title": s["name"], "description": s["playlist_description"] + " New every week on EzzyClips Ultra."}
            for s in SERIES.values()
        ],
    }


def setup_channel(execute: bool = False, business_email: str = "") -> dict:
    plan = planned_changes(business_email)
    if not execute:
        return {"mode": "dry_run", **plan}
    from .publisher import get_youtube_client
    client = get_youtube_client(MANAGE_SCOPES, env("YOUTUBE_MANAGE_TOKEN", ".secrets/token_manage.json"))
    mine = client.channels().list(part="brandingSettings", mine=True).execute()
    if not mine.get("items"):
        raise RuntimeError("No channel found for the authorized Google account")
    channel = mine["items"][0]
    branding = channel.get("brandingSettings", {})
    # channels.update replaces brandingSettings wholesale, so merge to keep title/banner.
    branding.setdefault("channel", {}).update(plan["brandingSettings"]["channel"])
    client.channels().update(part="brandingSettings", body={"id": channel["id"], "brandingSettings": branding}).execute()
    existing = {
        item["snippet"]["title"]
        for item in client.playlists().list(part="snippet", mine=True, maxResults=50).execute().get("items", [])
    }
    created = []
    for playlist in plan["playlists"]:
        if playlist["title"] in existing:
            continue
        client.playlists().insert(part="snippet,status", body={
            "snippet": {**playlist, "defaultLanguage": "en"},
            "status": {"privacyStatus": "public"},
        }).execute()
        created.append(playlist["title"])
    return {"mode": "applied", "channel_id": channel["id"], "playlists_created": created}
