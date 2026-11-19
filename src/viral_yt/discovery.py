"""Official YouTube Data API trend research (no downloads, no scraping).

Three lanes, all read-only:
  search -> search.list(order=viewCount, publishedAfter) per seed term, then videos.list enrichment
  chart  -> videos.list(chart=mostPopular, videoCategoryId) — cheap regional chart signal
  cc     -> search.list(videoLicense=creativeCommon): candidates whose uploader chose CC BY.
            These are the ONLY discovery results flagged `clip_candidate`, and they still need a
            ledger entry with evidence before anything can be rendered.
Scores are heuristics, not YouTube's ranking.
"""
from __future__ import annotations

import json
import math
import re
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .common import ROOT, env, get_logger, read_json, settings, write_csv

API_BASE = "https://www.googleapis.com/youtube/v3/"
COLUMNS = ["category", "rank", "score", "lane", "search_term", "video_id", "title", "channel", "channel_id", "url",
           "published_at", "age_hours", "duration_s", "is_short", "views", "views_per_hour", "likes", "comments",
           "engagement_rate", "channel_subs", "outlier_ratio", "license", "clip_candidate", "is_new", "times_seen",
           "angle_hint", "description"]
log = get_logger("discovery")


class DiscoveryError(RuntimeError):
    pass


class Api:
    """Tiny client with retries and a per-run call counter (for quota awareness)."""

    def __init__(self, api_key: str, opener=None):
        self.key = api_key
        self.calls: dict[str, int] = {}
        self._open = opener or urllib.request.urlopen

    def get(self, endpoint: str, params: dict, attempts: int = 3) -> dict:
        self.calls[endpoint] = self.calls.get(endpoint, 0) + 1
        url = API_BASE + endpoint + "?" + urllib.parse.urlencode({**params, "key": self.key})
        request = urllib.request.Request(url, headers={"User-Agent": "TrendTide/2.0"})
        for attempt in range(attempts):
            try:
                with self._open(request, timeout=25) as response:
                    return json.load(response)
            except urllib.error.HTTPError as exc:
                details = exc.read(400).decode("utf-8", "replace")
                if exc.code not in (429, 500, 502, 503, 504) or attempt == attempts - 1:
                    raise DiscoveryError(f"YouTube API HTTP {exc.code} on {endpoint}: {details}") from exc
            except (OSError, TimeoutError) as exc:
                if attempt == attempts - 1:
                    raise DiscoveryError(f"YouTube API network error: {exc}") from exc
            time.sleep(2 ** attempt)
        raise DiscoveryError("Unexpected API failure")


def iso_duration(value: str) -> int:
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", value or "")
    if not m:
        return 0
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s


def angle_hint(title: str) -> str:
    """Suggest an ORIGINAL angle for our own Short (we never repost the source)."""
    t = title.lower()
    if any(w in t for w in ("leak", "rumor", "rumour", "release date", "delay")):
        return "Myth vs Confirmed: separate official statements from speculation"
    if any(w in t for w in ("trailer", "reveal", "announce", "launch")):
        return "3 details you missed + why it matters (own visuals/screenshots under licence)"
    if any(w in t for w in ("vs", "versus", "compare", "better")):
        return "60-second verdict with your own test or data"
    if any(w in t for w in ("how", "tutorial", "guide")):
        return "Fastest working method, shown on your own screen recording"
    return "Why it blew up: context, timeline, what happens next"


def rank_video(item: dict, now: datetime | None = None, channel_subs: int | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    snippet, stats = item.get("snippet", {}), item.get("statistics", {})
    raw_date = snippet.get("publishedAt", now.isoformat())
    published = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
    age_hours = max((now - published).total_seconds() / 3600, 1.0)
    views, likes, comments = (int(stats.get(k, 0) or 0) for k in ("viewCount", "likeCount", "commentCount"))
    vph = views / age_hours
    engagement = (likes + comments * 3) / max(views, 1)
    freshness = max(0.0, 7.0 - age_hours / 24.0)
    outlier = (views / channel_subs) if channel_subs else 0.0
    # Heuristic: velocity dominates, engagement & "outlier vs channel size" break ties.
    score = math.log1p(vph) * 10 + min(engagement * 400, 20) + freshness * 2 + min(math.log1p(outlier) * 4, 12)
    duration = iso_duration(item.get("contentDetails", {}).get("duration", ""))
    license_ = item.get("status", {}).get("license", "")
    return {
        "video_id": item.get("id", ""), "title": snippet.get("title", ""), "channel": snippet.get("channelTitle", ""),
        "channel_id": snippet.get("channelId", ""), "url": "https://www.youtube.com/watch?v=" + item.get("id", ""),
        "published_at": raw_date, "age_hours": round(age_hours, 2), "duration_s": duration,
        "is_short": 0 < duration <= 180, "views": views, "views_per_hour": round(vph, 1), "likes": likes,
        "comments": comments, "engagement_rate": round(engagement, 5), "channel_subs": channel_subs or "",
        "outlier_ratio": round(outlier, 3) if channel_subs else "", "license": license_,
        "clip_candidate": license_ == "creativeCommon" and duration > 180,
        "score": round(score, 2), "angle_hint": angle_hint(snippet.get("title", "")),
        "description": snippet.get("description", "")[:350].replace("\n", " "),
    }


def _enrich(api: Api, ids: list[str]) -> list[dict]:
    items: list[dict] = []
    for i in range(0, len(ids), 50):
        chunk = ids[i:i + 50]
        items += api.get("videos", {"part": "snippet,statistics,contentDetails,status", "id": ",".join(chunk)}).get("items", [])
    return items


def _channel_subs(api: Api, channel_ids: set[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    ids = sorted(c for c in channel_ids if c)
    for i in range(0, len(ids), 50):
        data = api.get("channels", {"part": "statistics", "id": ",".join(ids[i:i + 50])})
        for ch in data.get("items", []):
            stats = ch.get("statistics", {})
            if not stats.get("hiddenSubscriberCount"):
                out[ch["id"]] = int(stats.get("subscriberCount", 0) or 0)
    return out


def discover(top: int | None = None, region: str | None = None, window_days: int | None = None,
             categories_file: Path | None = None, api_key: str | None = None, lanes: list[str] | None = None,
             api: Api | None = None, write: bool = True) -> list[dict]:
    cfg = settings()["discovery"]
    key = api_key or env("YOUTUBE_API_KEY")
    if api is None:
        if not key:
            raise DiscoveryError("Missing YOUTUBE_API_KEY. Copy .env.example to .env and configure it.")
        api = Api(key)
    top = top or cfg["top"]
    region = region or env("YOUTUBE_REGION", cfg["region"])
    window_days = window_days or int(env("TREND_WINDOW_DAYS", str(cfg["window_days"])))
    lanes = lanes or cfg["lanes"]
    if not (1 <= top <= 50 and 1 <= window_days <= 30):
        raise ValueError("top must be 1..50, window_days must be 1..30")
    topics = read_json(categories_file or ROOT / "config" / "topics.json")
    since = (datetime.now(timezone.utc) - timedelta(days=window_days)).strftime("%Y-%m-%dT%H:%M:%SZ")

    pool: dict[str, list[tuple[str, str, str]]] = {}   # video_id -> [(category, lane, term)]
    for category, terms in topics.items():
        for term in terms:
            base = {"part": "id", "q": term, "type": "video", "publishedAfter": since,
                    "maxResults": min(top * 3, 50), "regionCode": region, "relevanceLanguage": "en"}
            if "search" in lanes:
                for it in api.get("search", {**base, "order": "viewCount"}).get("items", []):
                    vid = it.get("id", {}).get("videoId")
                    if vid:
                        pool.setdefault(vid, []).append((category, "search", term))
            if "cc" in lanes:
                cc = {**base, "order": "viewCount", "videoLicense": "creativeCommon", "videoDuration": "long",
                      "maxResults": 10, "publishedAfter": (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")}
                for it in api.get("search", cc).get("items", []):
                    vid = it.get("id", {}).get("videoId")
                    if vid:
                        pool.setdefault(vid, []).append((category, "cc", term))
        chart_cat = cfg.get("chart_category_ids", {}).get(category)
        if "chart" in lanes and chart_cat:
            try:
                chart = api.get("videos", {"part": "id", "chart": "mostPopular", "regionCode": region,
                                           "videoCategoryId": chart_cat, "maxResults": 25})
                for it in chart.get("items", []):
                    pool.setdefault(it["id"], []).append((category, "chart", f"mostPopular:{chart_cat}"))
            except DiscoveryError as exc:  # some category/region combos are not chart-enabled
                log.warning("chart lane skipped for %s: %s", category, exc)

    items = _enrich(api, list(pool))
    subs = _channel_subs(api, {i.get("snippet", {}).get("channelId", "") for i in items}) if cfg.get("outlier_lookup") else {}
    by_cat: dict[str, dict[str, dict]] = {}
    for item in items:
        scored = rank_video(item, channel_subs=subs.get(item.get("snippet", {}).get("channelId", "")))
        for category, lane, term in pool.get(item["id"], []):
            row = {**scored, "category": category, "lane": lane, "search_term": term}
            prev = by_cat.setdefault(category, {}).get(row["video_id"])
            if prev is None or row["score"] > prev["score"]:
                by_cat[category][row["video_id"]] = row

    rows: list[dict] = []
    for category in topics:
        ranked = sorted(by_cat.get(category, {}).values(), key=lambda r: r["score"], reverse=True)[:top]
        for n, cand in enumerate(ranked, 1):
            cand["rank"] = n
        rows.extend(ranked)
    if write:
        from .state import remember_candidates
        remember_candidates(rows)
        out = ROOT / "output" / "trends"
        out.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        write_csv(out / "latest_trends.csv", rows, COLUMNS)
        write_csv(out / f"trends_{stamp}.csv", rows, COLUMNS)
        meta = {"generated_utc": stamp, "region": region, "window_days": window_days, "lanes": lanes,
                "api_calls": api.calls, "median_score": statistics.median([r["score"] for r in rows]) if rows else None,
                "note": "Ranked candidates (heuristic). Finding a video does NOT authorize clipping it.", "results": rows}
        (out / "latest_trends.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
        log.info("discover: %d rows, calls=%s", len(rows), api.calls)
    return rows
