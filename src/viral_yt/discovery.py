"""Official YouTube Data API trend research; no video downloads or scraped transcripts."""
from __future__ import annotations

import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .common import ROOT, env, read_json, write_csv

API_BASE = "https://www.googleapis.com/youtube/v3/"
COLUMNS = ["category", "rank", "score", "search_term", "video_id", "title", "channel", "url", "published_at", "age_hours", "views", "views_per_hour", "likes", "comments", "engagement_rate", "description"]


class DiscoveryError(RuntimeError):
    pass


def api_get(endpoint: str, params: dict, api_key: str, attempts: int = 3) -> dict:
    values = {**params, "key": api_key}
    request = urllib.request.Request(API_BASE + endpoint + "?" + urllib.parse.urlencode(values), headers={"User-Agent": "TrendTide60/0.1"})
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=25) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            details = exc.read(400).decode("utf-8", "replace")
            if exc.code not in (429, 500, 502, 503, 504) or attempt == attempts - 1:
                raise DiscoveryError(f"YouTube API HTTP {exc.code}: {details}") from exc
        except (OSError, TimeoutError) as exc:
            if attempt == attempts - 1:
                raise DiscoveryError(f"YouTube API network error: {exc}") from exc
        time.sleep(2**attempt)
    raise DiscoveryError("Unexpected API failure")


def rank_video(item: dict, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    snippet = item.get("snippet", {})
    stats = item.get("statistics", {})
    raw_date = snippet.get("publishedAt", now.isoformat())
    published = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
    age_hours = max((now - published).total_seconds() / 3600, 1.0)
    views = int(stats.get("viewCount", 0))
    likes = int(stats.get("likeCount", 0))
    comments = int(stats.get("commentCount", 0))
    vph = views / age_hours
    engagement_rate = (likes + comments) / max(views, 1)
    # Heuristic only; not a representation of YouTube's secret recommendation score.
    freshness = max(0.0, 7.0 - (age_hours / 24.0))
    score = math.log1p(vph) * 10 + min(engagement_rate * 500, 20) + freshness * 2
    return {
        "video_id": item.get("id", ""),
        "title": snippet.get("title", ""),
        "channel": snippet.get("channelTitle", ""),
        "url": "https://www.youtube.com/watch?v=" + item.get("id", ""),
        "published_at": raw_date,
        "age_hours": round(age_hours, 2),
        "views": views,
        "views_per_hour": round(vph, 1),
        "likes": likes,
        "comments": comments,
        "engagement_rate": round(engagement_rate, 5),
        "score": round(score, 2),
        "description": snippet.get("description", "")[:350],
    }


def discover(top: int = 10, region: str | None = None, window_days: int | None = None, categories_file: Path | None = None, api_key: str | None = None) -> list[dict]:
    key = api_key or env("YOUTUBE_API_KEY")
    if not key:
        raise DiscoveryError("Missing YOUTUBE_API_KEY. Copy .env.example to .env and configure it.")
    region = region or env("YOUTUBE_REGION", "CA")
    window_days = window_days or int(env("TREND_WINDOW_DAYS", "7"))
    if not (1 <= top <= 50 and 1 <= window_days <= 30):
        raise ValueError("top must be 1..50, window_days must be 1..30")
    topics = read_json(categories_file or ROOT / "config" / "topics.json")
    since = (datetime.now(timezone.utc) - timedelta(days=window_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows: list[dict] = []
    for category, terms in topics.items():
        found: dict[str, dict] = {}
        for term in terms:
            search = api_get("search", {
                "part": "id", "q": term, "type": "video", "order": "viewCount",
                "publishedAfter": since, "maxResults": min(top * 3, 50), "regionCode": region,
            }, key)
            ids = list(dict.fromkeys(
                video.get("id", {}).get("videoId", "") for video in search.get("items", [])
            ))
            ids = [item for item in ids if item]
            if not ids:
                continue
            details = api_get("videos", {"part": "snippet,statistics,contentDetails", "id": ",".join(ids)}, key)
            for video in details.get("items", []):
                scored = rank_video(video)
                scored.update(category=category, search_term=term)
                if scored["video_id"] not in found or scored["score"] > found[scored["video_id"]]["score"]:
                    found[scored["video_id"]] = scored
        ranked = sorted(found.values(), key=lambda row: row["score"], reverse=True)[:top]
        for n, candidate in enumerate(ranked, 1):
            candidate["rank"] = n
        rows.extend(ranked)
    output = ROOT / "output" / "trends"
    output.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    write_csv(output / "latest_trends.csv", rows, COLUMNS)
    (output / "latest_trends.json").write_text(json.dumps({"generated_utc": timestamp, "region": region, "window_days": window_days, "results": rows}, indent=2, ensure_ascii=False), encoding="utf-8")
    write_csv(output / f"trends_{timestamp}.csv", rows, COLUMNS)
    return rows
