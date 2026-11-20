"""Manual clipper mode: link (YouTube / finished live stream / direct file) or upload -> N viral clips with hooks,
captions, smart/active-speaker framing, in every requested aspect ratio. Shared by the CLI and the web worker."""
from __future__ import annotations

import json
from pathlib import Path

from .clipper import render_asset
from .ingest import (IngestError, download_direct, download_youtube, media_duration, parse_vtt_words, youtube_id,
                     youtube_info, youtube_rights)
from .media_prep import work_dir
from .rights import APPROVED_LICENSES


def _cached_youtube(vid: str, src_dir: Path, max_s: int, cache_dir, say):
    """Download once per video: a resubmitted link (e.g. after a failure) reuses the file instead of hitting
    YouTube again (which is what triggers HTTP 429). The cache is per user and expires with source retention."""
    import os
    import shutil
    if not cache_dir:
        say("downloading")
        return download_youtube(vid, src_dir, max_s)
    cache = Path(cache_dir) / vid
    vids = [p for p in cache.glob("source.*") if p.stem == "source" and p.suffix.lower() in (".mp4", ".mkv", ".webm", ".mov")]
    if vids:
        say("using the copy downloaded earlier")
        os.utime(cache)                                  # keep it alive while it's being used
    else:
        say("downloading")
        download_youtube(vid, cache, max_s)
    src_dir.mkdir(parents=True, exist_ok=True)
    out = []
    for f in cache.iterdir():
        if f.is_file() and (f.stem == "source" or f.suffix == ".vtt") and f.name.startswith("source"):
            dst = src_dir / f.name
            if not dst.exists():
                try:
                    os.link(f, dst)                      # no extra disk space (NTFS/ext4)
                except OSError:
                    shutil.copy2(f, dst)
            out.append(dst)
    video = next((p for p in out if p.stem == "source" and p.suffix.lower() in (".mp4", ".mkv", ".webm", ".mov")), None)
    if not video:
        raise IngestError("Download failed or the video exceeded the length limit")
    vtts = sorted(p for p in out if p.suffix == ".vtt")
    return video, (vtts[0] if vtts else None)


def run_link_job(spec: dict, out_dir: Path, verified_channels: set[str] | None = None, progress=None,
                 record: bool = False) -> list[dict]:
    say = progress or (lambda msg: None)
    out_dir.mkdir(parents=True, exist_ok=True)
    max_s = int(spec.get("max_seconds", 3 * 3600))
    owner, basis, evidence, words = spec.get("owner", ""), spec.get("rights_basis", ""), spec.get("rights_evidence", ""), None
    src_dir = out_dir / "_source"
    if spec.get("upload_path"):
        up = Path(spec["upload_path"])
        src_dir.mkdir(parents=True, exist_ok=True)
        source = src_dir / ("source" + up.suffix)
        if up.resolve() != source.resolve():
            if spec.get("keep_upload"):
                import shutil
                shutil.copy2(up, source)
            else:
                up.replace(source)                    # keep the source with the job for review re-trims
    elif spec.get("url") and (vid := youtube_id(spec["url"])):
        say("checking rights")
        info = youtube_info(vid)
        basis, evidence = youtube_rights(info, verified_channels or set())
        owner = info["channel"]
        if info["duration_s"] and info["duration_s"] > max_s:
            raise IngestError(f"Video is longer than your plan's {max_s // 60}-minute limit")
        source, vtt = _cached_youtube(vid, src_dir, max_s, spec.get("cache_dir"), say)
        if vtt:
            words = parse_vtt_words(vtt.read_text(encoding="utf-8", errors="replace"))
        spec.setdefault("source_video_url", f"https://www.youtube.com/watch?v={vid}")
        spec.setdefault("title_hint", info["title"])
    elif spec.get("url"):
        say("downloading")
        source = download_direct(spec["url"], src_dir, int(spec.get("max_bytes", 4 * 1024 ** 3)))
    else:
        raise IngestError("Provide a link or upload a file")
    if basis not in APPROVED_LICENSES or len(evidence.strip()) < 8:
        raise IngestError("Declare how you hold the rights to this video and add evidence (min 8 characters)")
    if media_duration(source) > max_s + 5:
        raise IngestError(f"Video is longer than your plan's {max_s // 60}-minute limit")
    if words:  # free word timings from captions -> skip local ASR
        (work_dir(source) / "words.json").write_text(json.dumps(words))
    asset = {"asset_id": spec["job_id"], "category": spec.get("category", ""), "source_file": str(source),
             "owner": owner or "the creator", "rights_basis": basis, "rights_evidence": evidence,
             "source_video_url": spec.get("source_video_url", spec.get("url", "")), "clip_ranges": "auto",
             "clip_count": spec.get("count", 5), "caption_style": spec.get("caption_style", "bold_pop"),
             "cold_open": spec.get("cold_open", True), "layout": spec.get("layout", "smart"),
             "approved": "true", "publish_approved": "false", "tags": spec.get("tags", "")}
    say("transcribing & finding highlights")
    return render_asset(asset, source, ratios=spec.get("ratios") or ["9:16"], out_dir=out_dir, record=record,
                        progress=say)
