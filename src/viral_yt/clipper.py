"""FFmpeg-only editing of authorized local sources into vertical Shorts."""
from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .common import ROOT, local_file, safe_name
from .rights import check_originality, load_asset, validate_asset
from .transcripts import parse_srt, seconds_to_srt


def check_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        raise RuntimeError("FFmpeg and ffprobe are required; install FFmpeg and add to PATH")


def probe_duration(media: Path) -> float:
    check_ffmpeg()
    result = subprocess.run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(media),
    ], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"ffprobe failed: {result.stderr[-800:]}")
    return float(result.stdout.strip())


def chunk_cues(cues: list[tuple[float, float, str]], max_words: int = 3) -> list[tuple[float, float, str]]:
    """Split caption cues into 2-4 word chunks for big, mobile-readable burned-in captions."""
    chunked = []
    for begin, end, text in cues:
        words = text.split()
        if len(words) <= max_words:
            chunked.append((begin, end, text))
            continue
        groups = [words[i:i + max_words] for i in range(0, len(words), max_words)]
        step = (end - begin) / len(groups)
        for n, group in enumerate(groups):
            chunked.append((begin + n * step, begin + (n + 1) * step, " ".join(group)))
    return chunked


def slice_srt(source: Path, clip_start: float, clip_end: float, target: Path) -> bool:
    cues = parse_srt(source.read_text(encoding="utf-8-sig"))
    selected = []
    for begin, end, text in cues:
        clipped_begin, clipped_end = max(begin, clip_start), min(end, clip_end)
        if clipped_begin < clipped_end:
            selected.append((clipped_begin - clip_start, clipped_end - clip_start, text))
    if not selected:
        return False
    selected = chunk_cues(selected)
    with target.open("w", encoding="utf-8") as out:
        for i, (begin, end, text) in enumerate(selected, 1):
            out.write(f"{i}\n{seconds_to_srt(begin)} --> {seconds_to_srt(end)}\n{text}\n\n")
    return True


def ffmpeg_filter_path(path: Path) -> str:
    """Escape FFmpeg filter filename separators, including Windows drive colon."""
    value = path.as_posix()
    # ffmpeg's filtergraph parser and the subtitle filter both parse colon/backslash.
    value = value.replace("\\", "\\\\").replace(":", "\\\\:")
    value = value.replace("'", "'\\''").replace(",", "\\,")
    return value


def render(asset_id: str, preset: str = "medium") -> Path:
    asset = load_asset(asset_id)
    source, start, end = validate_asset(asset)
    check_ffmpeg()
    actual = probe_duration(source)
    if end > actual + 0.25:
        raise RuntimeError(f"Clip end {end}s exceeds source duration {actual:.2f}s")
    output = ROOT / "output" / "shorts"
    output.mkdir(parents=True, exist_ok=True)
    output_file = output / f"{safe_name(asset_id)}.mp4"
    seconds = end - start
    commentary = (asset.get("commentary_audio") or "").strip()
    commentary_seconds = probe_duration(local_file(commentary)) if commentary else 0.0
    check_originality(asset, seconds, min(commentary_seconds, seconds))
    warnings = []
    if not 20 <= seconds <= 40:
        warnings.append(f"Short is {seconds:.0f}s; 20-40s loops best.")
    video_filter = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1,fps=30"
    captions_source = (asset.get("transcript_file") or "").strip()
    caption_file = output / f"{safe_name(asset_id)}.srt"
    has_captions = captions_source and slice_srt(local_file(captions_source), start, end, caption_file)
    if has_captions:
        # Large, bold, high-contrast captions sit above the bottom ~20% where YouTube's UI overlays.
        video_filter += f",subtitles=filename='{ffmpeg_filter_path(caption_file)}':force_style='Fontname=Arial,Fontsize=24,Bold=1,Alignment=2,MarginV=420,Outline=3,Shadow=1'"
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", str(start), "-i", str(source)]
    if commentary:
        cmd += ["-i", str(local_file(commentary))]
    cmd += ["-t", str(seconds), "-vf", video_filter, "-map", "0:v:0"]
    if commentary:
        cmd += ["-map", "1:a:0"]
    else:
        cmd += ["-map", "0:a:0?"]
    cmd += ["-c:v", "libx264", "-preset", preset, "-crf", "22", "-pix_fmt", "yuv420p", "-r", "30", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(output_file)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode or not output_file.exists():
        raise RuntimeError(f"FFmpeg render failed: {proc.stderr[-2000:]}")
    metadata = {
        "asset_id": asset_id, "rendered_utc": datetime.now(timezone.utc).isoformat(),
        "category": asset.get("category"), "title": asset.get("title"),
        "description": asset.get("description", ""), "rights_basis": asset.get("rights_basis"),
        "rights_evidence": asset.get("rights_evidence"), "source_video_url": asset.get("source_video_url", ""),
        "source_owner": asset.get("owner", ""), "publish_approved": asset.get("publish_approved"),
        "file": str(output_file), "duration_seconds": seconds, "captions_included": bool(has_captions),
        "original_commentary_included": bool(commentary),
        "commentary_share": round(min(commentary_seconds, seconds) / seconds, 2) if seconds else 0,
        "series": asset.get("series", ""), "warnings": warnings,
    }
    (output / f"{safe_name(asset_id)}.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return output_file
