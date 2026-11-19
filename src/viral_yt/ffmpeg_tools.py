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
            f"[lb_a]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},gblur=sigma=28,eq=brightness=-0.10[lb_bg];"
            f"[lb_b]scale={w}:-2:force_original_aspect_ratio=decrease[lb_fg];"
            f"[lb_bg][lb_fg]overlay=(W-w)/2:(H-h)/2-{int(h*0.06)},setsar=1,fps={fps}[{out}]")


def loudnorm(lufs: float) -> str:
    return f"loudnorm=I={lufs}:TP=-1.5:LRA=11"


def run(cmd: list[str], cwd: Path | None = None) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(cwd) if cwd else None)
    if proc.returncode:
        raise RuntimeError(f"FFmpeg failed ({proc.returncode}): {proc.stderr[-2500:]}")


def encode_args(crf: int, preset: str, fps: int) -> list[str]:
    return ["-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p", "-r", str(fps),
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart"]
