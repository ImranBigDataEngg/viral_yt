"""Machine-readable local clip distribution manifest."""
from __future__ import annotations

import json
from pathlib import Path
from .common import ROOT, write_csv

MANIFEST_COLUMNS = ["asset_id", "title", "category", "duration_seconds", "local_path", "download_link", "size_mb", "captions_included", "original_commentary_included", "source_owner", "rights_basis", "youtube_url", "instagram_status", "tiktok_status"]


def build_manifest() -> tuple[Path, list[dict]]:
    output_dir = ROOT / "output" / "shorts"
    records: list[dict] = []
    for metadata_file in sorted(output_dir.glob("*.json")):
        data = json.loads(metadata_file.read_text(encoding="utf-8"))
        video = Path(data["file"])
        if not video.is_file():
            continue
        url_file = video.with_suffix(".youtube.json")
        youtube_url = ""
        if url_file.is_file():
            youtube_url = json.loads(url_file.read_text(encoding="utf-8")).get("youtube_url", "")
        records.append({
            "asset_id": data.get("asset_id", ""), "title": data.get("title", ""),
            "category": data.get("category", ""), "duration_seconds": data.get("duration_seconds", ""),
            "local_path": str(video.resolve()), "download_link": video.resolve().as_uri(),
            "size_mb": round(video.stat().st_size / 1024**2, 2),
            "captions_included": data.get("captions_included", False),
            "original_commentary_included": data.get("original_commentary_included", False),
            "source_owner": data.get("source_owner", ""), "rights_basis": data.get("rights_basis", ""),
            "youtube_url": youtube_url, "instagram_status": "Not posted", "tiktok_status": "Not posted",
        })
    destination = ROOT / "output" / "manifests" / "download_manifest.csv"
    write_csv(destination, records, MANIFEST_COLUMNS)
    return destination, records
