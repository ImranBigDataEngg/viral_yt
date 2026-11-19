"""Manual clipper mode: turn a long-video LINK into an authorized local source file.

Accepted (rights are checked BEFORE anything is downloaded):
  * YouTube video / finished live stream on a channel the user has VERIFIED via Google OAuth  -> rights "original"
  * YouTube video whose uploader chose Creative Commons BY (API status.license)              -> rights "cc_by" (+credit)
  * Direct https video file link (your CDN, Drive/Dropbox direct link) with a declared rights basis + evidence
  * File upload (web) / local path (CLI) with a declared rights basis + evidence
Rejected: someone else's standard-licence YouTube video. Live streams still in progress are queued until the VOD exists.
Network safety: https only, no credentials in URLs, every redirect hop re-validated, private/loopback/link-local/
reserved IPs blocked (SSRF), size and duration caps.
"""
from __future__ import annotations

import ipaddress
import json
import re
import socket
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .common import env

YT_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "music.youtube.com"}
VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}


class IngestError(ValueError):
    pass


class LiveNotFinished(IngestError):
    """Raised for a live/upcoming broadcast; the job is retried later."""


def youtube_id(url: str) -> str | None:
    try:
        u = urllib.parse.urlsplit(url.strip())
    except ValueError:
        return None
    host = (u.hostname or "").lower()
    if host not in YT_HOSTS:
        return None
    if host == "youtu.be":
        cand = u.path.strip("/").split("/")[0]
    elif u.path == "/watch":
        cand = urllib.parse.parse_qs(u.query).get("v", [""])[0]
    else:
        m = re.match(r"^/(?:live|shorts|embed|v)/([^/?#]+)", u.path)
        cand = m.group(1) if m else ""
    return cand if VIDEO_ID.match(cand or "") else None


def _public_ip(host: str) -> None:
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise IngestError(f"Cannot resolve host {host}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global or ip.is_multicast:
            raise IngestError("Links to private, local or reserved network addresses are not allowed")


def validate_public_url(url: str) -> urllib.parse.SplitResult:
    if len(url) > 2048:
        raise IngestError("URL too long")
    u = urllib.parse.urlsplit(url.strip())
    if u.scheme != "https":
        raise IngestError("Only https:// links are accepted")
    if u.username or u.password:
        raise IngestError("Links containing credentials are not accepted")
    if u.port not in (None, 443):
        raise IngestError("Only the default https port is allowed")
    if not u.hostname:
        raise IngestError("Invalid link")
    _public_ip(u.hostname)
    return u


def youtube_info(video_id: str, api_key: str | None = None, opener=None) -> dict:
    """Metadata needed for the rights check (no media is downloaded). Uses the YouTube Data API when
    YOUTUBE_API_KEY is set, otherwise yt-dlp's metadata-only mode."""
    key = api_key or env("YOUTUBE_API_KEY")
    if not key:
        return _youtube_info_ytdlp(video_id)
    q = urllib.parse.urlencode({"part": "snippet,status,contentDetails,liveStreamingDetails", "id": video_id, "key": key})
    with (opener or urllib.request.urlopen)(f"https://www.googleapis.com/youtube/v3/videos?{q}", timeout=20) as r:
        items = json.load(r).get("items", [])
    if not items:
        raise IngestError("Video not found, private, or removed")
    it = items[0]
    from .discovery import iso_duration
    return {"video_id": video_id, "title": it["snippet"].get("title", ""), "channel_id": it["snippet"].get("channelId", ""),
            "channel": it["snippet"].get("channelTitle", ""), "license": it.get("status", {}).get("license", ""),
            "live": it["snippet"].get("liveBroadcastContent", "none"),
            "duration_s": iso_duration(it.get("contentDetails", {}).get("duration", ""))}


def ytdlp_to_info(video_id: str, meta: dict) -> dict:
    """Map yt-dlp metadata to the same shape as the Data API result (pure, unit-tested)."""
    live_status = meta.get("live_status") or ""
    live = ("live" if live_status in ("is_live", "post_live") else "upcoming" if live_status == "is_upcoming"
            else "none")
    lic = (meta.get("license") or "").lower()
    return {"video_id": video_id, "title": meta.get("title") or "", "channel_id": meta.get("channel_id") or "",
            "channel": meta.get("channel") or meta.get("uploader") or "",
            "license": "creativeCommon" if "creative commons" in lic else "youtube",
            "live": live, "duration_s": int(meta.get("duration") or 0)}


def _youtube_info_ytdlp(video_id: str) -> dict:
    try:
        import yt_dlp
    except ImportError as exc:
        raise IngestError("Checking YouTube links needs YOUTUBE_API_KEY in .env or yt-dlp installed "
                          "(pip install yt-dlp)") from exc
    try:
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True}) as ydl:
            meta = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
    except Exception as exc:  # yt-dlp raises many error types
        raise IngestError(f"Couldn't read that YouTube video: {str(exc)[:200]}") from exc
    return ytdlp_to_info(video_id, meta or {})


def youtube_rights(info: dict, verified_channel_ids: set[str]) -> tuple[str, str]:
    """-> (rights_basis, evidence). Raises IngestError when the user has no right to clip it."""
    if info["live"] in ("live", "upcoming"):
        raise LiveNotFinished("This live stream hasn't finished yet — the job will start automatically when the "
                              "replay (VOD) is available.")
    if info["channel_id"] and info["channel_id"] in verified_channel_ids:
        return "original", f"Channel ownership verified via Google OAuth ({info['channel_id']})"
    if info["license"] == "creativeCommon":
        return "cc_by", f"YouTube Creative Commons BY licence on {info['video_id']} by {info['channel']}"
    raise IngestError("This video belongs to a channel you haven't verified and isn't Creative Commons licensed. "
                      "Connect the channel that owns it, or upload the file with the creator's written permission.")


def download_youtube(video_id: str, dest: Path, max_seconds: int, with_subs: bool = True) -> tuple[Path, Path | None]:
    """Download an AUTHORIZED video (after youtube_rights passed) with yt-dlp; no cookies are ever used."""
    try:
        import yt_dlp
    except ImportError as exc:
        raise IngestError("Link mode needs yt-dlp: pip install yt-dlp") from exc
    dest.mkdir(parents=True, exist_ok=True)

    def too_long(info, *, incomplete=False):
        d = info.get("duration") or 0
        return f"longer than the {max_seconds // 60}-minute limit" if d and d > max_seconds else None

    opts = {"format": "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080][ext=mp4]/b[height<=1080]/b",
            "merge_output_format": "mp4", "outtmpl": str(dest / "source.%(ext)s"), "noplaylist": True,
            "quiet": True, "no_warnings": True, "restrictfilenames": True, "match_filter": too_long,
            "writesubtitles": with_subs, "writeautomaticsub": with_subs, "subtitleslangs": ["en", "en-orig", "en.*"],
            "subtitlesformat": "vtt", "max_filesize": 6 * 1024 ** 3, "retries": 3}
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([f"https://www.youtube.com/watch?v={video_id}"])
    vids = [p for p in dest.glob("source.*") if p.suffix.lower() in VIDEO_EXT]
    if not vids:
        raise IngestError("Download failed or the video exceeded the length limit")
    subs = sorted(dest.glob("source*.vtt"))
    return vids[0], (subs[0] if subs else None)


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_public_url(newurl)  # re-check every hop (SSRF via redirect)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download_direct(url: str, dest: Path, max_bytes: int) -> Path:
    validate_public_url(url)
    dest.mkdir(parents=True, exist_ok=True)
    opener = urllib.request.build_opener(_SafeRedirect)
    req = urllib.request.Request(url, headers={"User-Agent": "TrendTide/2.2"})
    try:
        with opener.open(req, timeout=30) as r:
            ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if not (ctype.startswith("video/") or ctype in ("application/octet-stream", "binary/octet-stream")):
                raise IngestError(f"Link is not a video file (content-type {ctype or 'unknown'})")
            if int(r.headers.get("Content-Length") or 0) > max_bytes:
                raise IngestError("File is larger than your plan allows")
            out = dest / "source.upload"
            total = 0
            with out.open("wb") as f:
                while chunk := r.read(1 << 20):
                    total += len(chunk)
                    if total > max_bytes:
                        f.close()
                        out.unlink(missing_ok=True)
                        raise IngestError("File is larger than your plan allows")
                    f.write(chunk)
    except urllib.error.URLError as exc:
        raise IngestError(f"Download failed: {exc.reason}") from exc
    return finalize_media(out)


def finalize_media(path: Path) -> Path:
    """ffprobe-validate an uploaded/downloaded file and give it the right extension. Rejects non-video."""
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type:format=format_name,duration",
                        "-of", "json", str(path)], capture_output=True, text=True, timeout=60)
    try:
        data = json.loads(r.stdout or "{}")
    except json.JSONDecodeError:
        data = {}
    if not any(s.get("codec_type") == "video" for s in data.get("streams", [])):
        path.unlink(missing_ok=True)
        raise IngestError("That file isn't a playable video")
    fmt = data.get("format", {}).get("format_name", "")
    ext = ".mp4" if "mp4" in fmt or "mov" in fmt else ".webm" if "webm" in fmt else ".mkv"
    final = path.with_suffix(ext)
    path.replace(final)
    return final


def media_duration(path: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def parse_vtt_words(text: str) -> list[tuple[float, float, str]]:
    """YouTube auto-caption VTT carries per-word timings (<00:00:01.234><c> word</c>) — free word timestamps,
    so link jobs usually skip local ASR entirely. Plain VTT cues fall back to proportional spreading."""
    from .transcripts import words_from_cues

    def ts(v: str) -> float:
        parts = v.replace(",", ".").split(":")
        parts = ["0"] * (3 - len(parts)) + parts
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])

    words, plain, seen = [], [], set()
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n")):
        m = re.search(r"(\d[\d:.,]+)\s+-->\s+(\d[\d:.,]+)", block)
        if not m:
            continue
        start, end = ts(m.group(1)), ts(m.group(2))
        body = block[m.end():].strip()
        if "<c>" in body or re.search(r"<\d\d:\d\d", body):
            line = next((ln for ln in body.splitlines() if "<" in ln), "")
            tokens = re.split(r"(<\d[\d:.]+>)", line)
            cur_t, pending = start, []
            for tok in tokens:
                if re.fullmatch(r"<\d[\d:.]+>", tok):
                    nt = ts(tok[1:-1])
                    for w in pending:
                        words.append((cur_t, nt, w))
                    cur_t, pending = nt, []
                else:
                    pending += [w for w in re.sub(r"</?c[^>]*>", "", tok).split() if w]
            for w in pending:
                words.append((cur_t, end, w))
        else:
            clean = re.sub(r"<[^>]+>", "", body).strip()
            key = (round(start, 2), clean)
            if clean and key not in seen:
                seen.add(key)
                plain.append((start, end, " ".join(clean.split())))
    if words:
        words.sort()
        dedup = [words[0]]
        for w in words[1:]:
            if not (abs(w[0] - dedup[-1][0]) < 0.01 and w[2] == dedup[-1][2]):
                dedup.append(w)
        return dedup
    return words_from_cues(plain)
