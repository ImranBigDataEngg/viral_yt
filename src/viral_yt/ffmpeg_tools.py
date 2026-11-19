"""Thin FFmpeg helpers shared by the clip and explainer renderers."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def check_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        raise RuntimeError("FFmpeg and ffprobe are required; on Windows: winget install Gyan.FFmpeg, then reopen PowerShell")


def probe_duration(media: Path) -> float:
    check_ffmpeg()
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                        "default=noprint_wrappers=1:nokey=1", str(media)], capture_output=True, text=True)
    if r.returncode or not r.stdout.strip():
        raise RuntimeError(f"ffprobe failed for {media}: {r.stderr[-600:]}")
    return float(r.stdout.strip())


def has_audio(media: Path) -> bool:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of",
                        "csv=p=0", str(media)], capture_output=True, text=True)
    return bool(r.stdout.strip())


def ffmpeg_filter_path(path: Path) -> str:
    """Escape a path for use inside a filtergraph (handles Windows drive colons)."""
    value = path.as_posix().replace("\\", "\\\\").replace(":", "\\\\:")
    return value.replace("'", "'\\''").replace(",", "\\,")


def layout_chain(src: str, layout: str, w: int, h: int, fps: int, out: str) -> str:
    """Return a filter_complex fragment mapping [src] -> [out] at w x h.

    crop    : fill & centre-crop (best for already-vertical or face-centred footage)
    blurfit : full frame kept, blurred zoomed copy behind (best for 16:9 gameplay/screens: keeps HUD/text)
    """
    if layout == "crop":
        return (f"[{src}]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1,fps={fps}[{out}]")
    return (f"[{src}]split=2[lb_a][lb_b];"
            # blur at 1/6 resolution then upscale: visually identical background, ~10x cheaper than full-res gblur
            f"[lb_a]scale={w // 6}:{h // 6}:force_original_aspect_ratio=increase,crop={w // 6}:{h // 6},"
            f"boxblur=6:2,scale={w}:{h}:flags=bilinear,eq=brightness=-0.10[lb_bg];"
            f"[lb_b]scale={w}:-2:force_original_aspect_ratio=decrease[lb_fg];"
            f"[lb_bg][lb_fg]overlay=(W-w)/2:(H-h)/2-{int(h*0.06)},setsar=1,fps={fps}[{out}]")


def loudnorm(lufs: float) -> str:
    return f"loudnorm=I={lufs}:TP=-1.5:LRA=11"


def run(cmd: list[str], cwd: Path | None = None) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(cwd) if cwd else None)
    if proc.returncode:
        raise RuntimeError(f"FFmpeg failed ({proc.returncode}): {proc.stderr[-2500:]}")


AUDIO_ARGS = ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart"]
HW_CANDIDATES = ["h264_nvenc", "h264_qsv", "h264_amf", "h264_videotoolbox"]


def probe_hw_encoder(cache: Path | None = None) -> str | None:
    """Functionally test GPU encoders (listing != working: drivers can be too old). Cached per FFmpeg build."""
    import json
    ver = subprocess.run(["ffmpeg", "-hide_banner", "-version"], capture_output=True, text=True).stdout.split("\n")[0]
    if cache and cache.is_file():
        data = json.loads(cache.read_text())
        if data.get("ffmpeg") == ver:
            return data.get("encoder")
    listed = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    found = None
    for enc in HW_CANDIDATES:
        if enc not in listed:
            continue
        r = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                            "color=c=black:s=640x360:d=0.4", "-c:v", enc, "-f", "null", "-"], capture_output=True)
        if r.returncode == 0:
            found = enc
            break
    if cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"ffmpeg": ver, "encoder": found}))
    return found


def video_args(crf: int, preset: str, fps: int, encoder: str | None = None) -> list[str]:
    q = str(crf + 2)
    if encoder == "h264_nvenc":
        v = ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", q, "-b:v", "0", "-pix_fmt", "yuv420p"]
    elif encoder == "h264_qsv":
        v = ["-c:v", "h264_qsv", "-global_quality", q, "-preset", "medium"]
    elif encoder == "h264_amf":
        v = ["-c:v", "h264_amf", "-quality", "balanced", "-rc", "cqp", "-qp_i", q, "-qp_p", q]
    elif encoder == "h264_videotoolbox":
        v = ["-c:v", "h264_videotoolbox", "-q:v", "62", "-pix_fmt", "yuv420p"]
    else:
        v = ["-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p"]
    return v + ["-r", str(fps), "-g", str(fps * 2)]


def encode_args(crf: int, preset: str, fps: int, encoder: str | None = None) -> list[str]:
    return video_args(crf, preset, fps, encoder) + AUDIO_ARGS


def layout_fit(src: str, w: int, h: int, fps: int, out: str, tag: str = "lb") -> str:
    return layout_chain(src, "blurfit", w, h, fps, out).replace("lb_", f"{tag}_")
