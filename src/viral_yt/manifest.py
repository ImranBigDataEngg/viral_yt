"""Download manifest (CSV) + offline HTML gallery with per-platform captions for cross-posting."""
from __future__ import annotations

import html
import json
import os
from pathlib import Path

from .common import ROOT, settings, write_csv
from .state import share_for, upload_for

MANIFEST_COLUMNS = ["output_id", "kind", "title", "category", "duration_seconds", "originality_score", "local_path",
                    "download_link", "share_link", "size_mb", "captions_included", "voice", "source_owner",
                    "rights_basis", "youtube_url", "youtube_privacy", "tiktok_caption", "instagram_caption",
                    "tiktok_status", "instagram_status"]


def captions_for(meta: dict) -> tuple[str, str]:
    tags = [t for t in meta.get("tags", []) if t][:4]
    hashtags = " ".join("#" + "".join(ch for ch in t.title() if ch.isalnum()) for t in tags)
    credit = f" Footage: {meta['source_owner']}." if meta.get("rights_basis") not in ("original", None) and meta.get("source_owner") else ""
    base = (meta.get("title") or "").strip()
    tiktok = f"{base}{credit} {hashtags} #fyp".strip()
    insta = f"{base}\n\n{(meta.get('description') or '').split(chr(10))[0]}{credit}\n\n{hashtags} #reels".strip()
    return tiktok[:2150], insta[:2150]


def build_manifest() -> tuple[Path, list[dict]]:
    out_dir = ROOT / "output" / "shorts"
    man_dir = ROOT / "output" / "manifests"
    man_dir.mkdir(parents=True, exist_ok=True)
    records = []
    for side in sorted(out_dir.glob("*.json")):
        if side.name.endswith(".youtube.json"):
            continue
        meta = json.loads(side.read_text(encoding="utf-8"))
        video = Path(meta.get("file", ""))
        if not video.is_file():
            continue
        oid = meta.get("output_id") or meta.get("asset_id")
        up, sh = upload_for(oid), share_for(oid)
        legacy = video.with_suffix(".youtube.json")
        yurl = (up or {}).get("url") or (json.loads(legacy.read_text()).get("youtube_url", "") if legacy.is_file() else "")
        tiktok, insta = captions_for(meta)
        records.append({
            "output_id": oid, "kind": meta.get("kind", "clip"), "title": meta.get("title", ""),
            "category": meta.get("category", ""), "duration_seconds": meta.get("duration_seconds", ""),
            "originality_score": meta.get("originality_score", ""), "local_path": str(video.resolve()),
            "download_link": video.resolve().as_uri(), "share_link": (sh or {}).get("link", ""),
            "size_mb": round(video.stat().st_size / 1024 ** 2, 2), "captions_included": meta.get("captions_included", False),
            "voice": meta.get("voice") or "", "source_owner": meta.get("source_owner", ""),
            "rights_basis": meta.get("rights_basis", ""), "youtube_url": yurl, "youtube_privacy": (up or {}).get("privacy", ""),
            "tiktok_caption": tiktok, "instagram_caption": insta, "tiktok_status": "Not posted", "instagram_status": "Not posted",
            "_rel": os.path.relpath(video.resolve(), man_dir.resolve()).replace("\\", "/"),
        })
    dest = man_dir / "download_manifest.csv"
    write_csv(dest, records, MANIFEST_COLUMNS)
    write_gallery(man_dir / "index.html", records)
    return dest, records


def write_gallery(path: Path, rows: list[dict]) -> Path:
    brand = settings()["brand"]["name"]
    cards = []
    for r in rows:
        e = lambda k: html.escape(str(r.get(k, "")))  # noqa: E731
        links = [f'<a class="btn" href="{html.escape(r["_rel"])}" download>⬇ Download MP4</a>']
        if r.get("share_link"):
            links.append(f'<a class="btn alt" href="{e("share_link")}">☁ Drive link</a>')
        if r.get("youtube_url"):
            links.append(f'<a class="btn alt" href="{e("youtube_url")}">▶ YouTube ({e("youtube_privacy")})</a>')
        cards.append(f"""<article><video src="{html.escape(r['_rel'])}" controls preload="metadata"></video>
<h3>{e('title')}</h3><p class="meta">{e('category')} · {e('duration_seconds')}s · originality {e('originality_score')}</p>
<div class="row">{''.join(links)}</div>
<label>TikTok caption</label><textarea readonly onclick="this.select()">{e('tiktok_caption')}</textarea>
<label>Instagram caption</label><textarea readonly onclick="this.select()">{e('instagram_caption')}</textarea></article>""")
    path.write_text(f"""<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{brand} — export gallery</title><style>
body{{margin:0;font-family:system-ui,Segoe UI,sans-serif;background:#0b1020;color:#e5e7eb}}
header{{padding:20px 24px;border-bottom:1px solid #1f2937}}h1{{margin:0;font-size:20px}}h1 span{{color:#22d3ee}}
main{{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:18px;padding:24px}}
article{{background:#111827;border:1px solid #1f2937;border-radius:14px;padding:12px}}
video{{width:100%;aspect-ratio:9/16;background:#000;border-radius:10px}}h3{{font-size:15px;margin:10px 0 4px}}
.meta{{color:#9ca3af;font-size:12px;margin:0 0 8px}}.row{{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px}}
.btn{{background:#22d3ee;color:#0b1020;text-decoration:none;font-weight:600;font-size:13px;padding:6px 10px;border-radius:8px}}
.btn.alt{{background:#1f2937;color:#e5e7eb}}label{{font-size:11px;color:#9ca3af}}
textarea{{width:100%;box-sizing:border-box;height:56px;background:#0b1020;color:#e5e7eb;border:1px solid #374151;border-radius:6px;font-size:12px}}
</style><header><h1><span>{brand}</span> export gallery — {len(rows)} Shorts</h1></header><main>{''.join(cards) or '<p>No renders yet.</p>'}</main></html>""",
                    encoding="utf-8")
    return path
