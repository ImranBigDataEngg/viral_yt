"""Generate original synthetic demo media locally and test render without external API keys."""
from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from viral_yt.common import ROOT
from viral_yt.clipper import render, check_ffmpeg
from viral_yt.manifest import build_manifest


def main():
    check_ffmpeg()
    folder = ROOT / "media/authorized"
    folder.mkdir(parents=True, exist_ok=True)
    src = folder / "demo_original.mp4"
    srt = ROOT / "media/transcripts/demo_original.srt"
    srt.parent.mkdir(parents=True, exist_ok=True)
    if not src.exists():
        subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=540x960:rate=30",
            "-f", "lavfi", "-i", "sine=frequency=330:sample_rate=44100",
            "-t", "8", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-pix_fmt", "yuv420p", str(src)
        ], check=True)
    srt.write_text("1\n00:00:00,000 --> 00:00:03,000\nA demo of TrendTide 60.\n\n2\n00:00:03,000 --> 00:00:06,000\nOriginal synthetic footage only.\n", encoding="utf-8")
    ledger = folder / "assets.csv"
    fields = ["asset_id", "category", "source_file", "source_video_url", "owner", "rights_basis", "rights_evidence", "transcript_file", "commentary_audio", "clip_start", "clip_end", "title", "description", "approved", "publish_approved"]
    row = dict(asset_id="synthetic_demo", category="Demo", source_file="media/authorized/demo_original.mp4", source_video_url="", owner="TrendTide 60 demo", rights_basis="original", rights_evidence="Synthetic video generated locally with FFmpeg", transcript_file="media/transcripts/demo_original.srt", commentary_audio="", clip_start="0", clip_end="6", title="TrendTide 60: synthetic demo", description="Unpublished, synthetic demo for pipeline validation.", approved="true", publish_approved="false")
    existing = []
    if ledger.exists():
        with ledger.open(newline="", encoding="utf-8-sig") as f:
            existing = [r for r in csv.DictReader(f) if r.get("asset_id") != "synthetic_demo"]
    with ledger.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(existing + [row])
    result = render("synthetic_demo", preset="ultrafast")
    manifest_path, entries = build_manifest()
    print(f"Created synthetic render: {result} ({result.stat().st_size:,} bytes)")
    print(f"Manifest: {manifest_path}, {len(entries)} entries")
    print("Not uploaded anywhere. No actual viral content or third-party media used.")


if __name__ == "__main__":
    main()
