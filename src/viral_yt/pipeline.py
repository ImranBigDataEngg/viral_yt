"""One-command daily cycle. Safe by default: nothing is uploaded without --execute, nothing public
without --publish + per-item approval + originality threshold.

  discover -> write briefs (top N/category) -> render approved briefs & approved ledger clips
  -> (optional) private upload -> (optional) Drive share links -> manifest + gallery
"""
from __future__ import annotations

import json
from pathlib import Path

from .common import ROOT, env, get_logger, is_true, settings
from .state import connect, published_titles

log = get_logger("pipeline")


def _rendered_ids() -> set[str]:
    with connect() as con:
        return {r[0] for r in con.execute("SELECT asset_id FROM renders")}


def run_cycle(discover_new: bool = True, per_category_briefs: int = 3, execute: bool = False,
              publish: bool = False, share: bool = False) -> dict:
    summary: dict = {"discovered": 0, "briefs": 0, "rendered": [], "uploaded": [], "shared": [], "errors": []}
    if discover_new and env("YOUTUBE_API_KEY"):
        from .discovery import discover
        from .scriptgen import write_briefs
        try:
            rows = discover()
            summary["discovered"] = len(rows)
            summary["briefs"] = len(write_briefs(rows, per_category_briefs))
        except Exception as exc:
            summary["errors"].append(f"discover: {exc}")
    elif discover_new:
        summary["errors"].append("discover skipped: YOUTUBE_API_KEY not set")

    done = _rendered_ids()
    titles = published_titles()
    from .composer import compose
    from .scriptgen import check_ready
    for bf in sorted((ROOT / "output" / "briefs").glob("*.json")):
        brief = json.loads(bf.read_text(encoding="utf-8"))
        if brief.get("brief_id") in done or check_ready(brief):
            continue
        try:
            summary["rendered"].append(str(compose(str(bf), published_titles=titles)))
        except Exception as exc:
            summary["errors"].append(f"compose {bf.name}: {exc}")

    from .clipper import render_all
    from .rights import DEFAULT_LEDGER, load_ledger
    if DEFAULT_LEDGER.exists():
        for asset in load_ledger():
            if not is_true(asset.get("approved")) or asset.get("asset_id") in done:
                continue
            try:
                summary["rendered"] += [str(p) for p in render_all(asset["asset_id"], published_titles=titles)]
            except Exception as exc:
                summary["errors"].append(f"render {asset.get('asset_id')}: {exc}")

    if execute or share:
        from .publisher import upload
        from .share import share_file
        with connect() as con:
            outputs = [dict(r) for r in con.execute("SELECT output_id, file FROM renders ORDER BY rendered_utc")]
        for o in outputs:
            if execute:
                try:
                    r = upload(o["output_id"], execute=True, publish=publish)
                    if r.get("youtube_url"):
                        summary["uploaded"].append(r["youtube_url"])
                except Exception as exc:
                    summary["errors"].append(f"upload {o['output_id']}: {exc}")
                    if "cap" in str(exc):
                        break
            if share and settings()["share"]["engine"] != "none":
                try:
                    summary["shared"].append(share_file(o["output_id"], Path(o["file"])))
                except Exception as exc:
                    summary["errors"].append(f"share {o['output_id']}: {exc}")

    from .manifest import build_manifest
    path, rows = build_manifest()
    summary["manifest"], summary["gallery"], summary["items"] = str(path), str(path.with_name("index.html")), len(rows)
    log.info("cycle: %s", summary)
    return summary
