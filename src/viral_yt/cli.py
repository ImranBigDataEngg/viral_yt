"""Command line entry point: python -m viral_yt.cli <command>  (or `trendtide <command>` after pip install -e .)"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .common import ROOT, read_csv


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="trendtide", description="TrendTide | trend radar + original/permissioned Shorts studio")
    sub = p.add_subparsers(dest="command", required=True)

    d = sub.add_parser("discover", help="Rank trending YouTube videos per category (official API, read-only)")
    d.add_argument("--top", type=int); d.add_argument("--region"); d.add_argument("--days", type=int)
    d.add_argument("--topics", type=Path); d.add_argument("--lanes", help="comma list: search,chart,cc")

    b = sub.add_parser("brief", help="Write original Short briefs from the latest trend CSV")
    b.add_argument("--per-category", type=int, default=3)

    c = sub.add_parser("compose", help="Render an APPROVED explainer brief (voice-over + captions)")
    c.add_argument("--brief", required=True)
    c.add_argument("--force", action="store_true", help="Preview render even if brief not approved (never uploaded)")

    hl = sub.add_parser("suggest-highlights", help="Suggest self-contained clip windows from an SRT")
    hl.add_argument("--srt", type=Path, required=True); hl.add_argument("--count", type=int, default=3)
    hl.add_argument("--media", type=Path, help="Optional media file for audio-energy signal")

    for name in ("validate", "render", "transcribe"):
        x = sub.add_parser(name, help=f"{name} a rights-ledger asset")
        x.add_argument("--asset-id", required=True)

    u = sub.add_parser("upload", help="Dry-run by default; --execute uploads PRIVATE")
    u.add_argument("--id", "--asset-id", dest="output_id", required=True, help="output_id (sidecar name in output/shorts)")
    u.add_argument("--execute", action="store_true"); u.add_argument("--publish", action="store_true")
    u.add_argument("--publish-at", help="ISO time to schedule (e.g. 2026-11-21T14:00:00Z)")

    s = sub.add_parser("share", help="Upload an output to your Google Drive and get a link (optional)")
    s.add_argument("--id", dest="output_id", required=True)

    sub.add_parser("manifest", help="Write download CSV + HTML gallery with platform captions")

    dr = sub.add_parser("doctor", help="Check FFmpeg/libass/GPU/models/keys and print exact fixes")
    dr.add_argument("--fetch-models", action="store_true", help="Download the YuNet face model (230 KB, MIT)")

    r = sub.add_parser("run", help="Full daily cycle (safe defaults)")
    r.add_argument("--no-discover", action="store_true"); r.add_argument("--per-category", type=int, default=3)
    r.add_argument("--execute", action="store_true"); r.add_argument("--publish", action="store_true")
    r.add_argument("--share", action="store_true")
    args = p.parse_args(argv)

    try:
        if args.command == "discover":
            from .discovery import discover
            rows = discover(args.top, args.region, args.days, args.topics, lanes=args.lanes.split(",") if args.lanes else None)
            _print({"candidates": len(rows), "csv": str(ROOT / "output/trends/latest_trends.csv"),
                    "clip_candidates_cc": sum(1 for r in rows if r["clip_candidate"]),
                    "per_category": {k: sum(1 for r in rows if r["category"] == k) for k in sorted({r["category"] for r in rows})}})
        elif args.command == "brief":
            from .scriptgen import write_briefs
            rows = read_csv(ROOT / "output/trends/latest_trends.csv")
            _print({"briefs": [str(x) for x in write_briefs(rows, args.per_category)]})
        elif args.command == "compose":
            from .composer import compose
            out = compose(args.brief, force=args.force)
            _print({"rendered": str(out), "sidecar": str(out.with_suffix(".json"))})
        elif args.command == "suggest-highlights":
            from .highlights import audio_energy, find_highlights
            from .transcripts import parse_srt
            cues = parse_srt(args.srt.read_text(encoding="utf-8-sig"))
            _print(find_highlights(cues, count=args.count, energy=audio_energy(args.media) if args.media else None))
        elif args.command == "validate":
            from .rights import load_asset, validate_asset
            video, start, end = validate_asset(load_asset(args.asset_id))
            _print({"valid": True, "file": str(video), "clip_start": start, "clip_end": end})
        elif args.command == "render":
            from .clipper import render_all
            from .state import published_titles
            _print({"rendered": [str(x) for x in render_all(args.asset_id, published_titles=published_titles())]})
        elif args.command == "transcribe":
            from .rights import load_asset, validate_asset
            from .transcripts import transcribe_owned_file
            path, _, _ = validate_asset(load_asset(args.asset_id))
            _print({"srt": str(transcribe_owned_file(path, ROOT / "media/transcripts" / f"{args.asset_id}.srt"))})
        elif args.command == "upload":
            from .publisher import upload
            _print(upload(args.output_id, args.execute, args.publish, args.publish_at))
        elif args.command == "share":
            from .publisher import load_meta
            from .share import share_file
            meta = load_meta(args.output_id)
            _print({"link": share_file(meta["output_id"], Path(meta["file"]))})
        elif args.command == "manifest":
            from .manifest import build_manifest
            f, rows = build_manifest()
            _print({"csv": str(f), "gallery": str(f.with_name("index.html")), "items": len(rows)})
        elif args.command == "doctor":
            from .doctor import fetch_models, run_checks
            if args.fetch_models:
                print("downloaded:", fetch_models())
            rows = run_checks()
            for r in rows:
                print(f"{'OK ' if r['ok'] else 'FIX'}  {r['check']:<24} {r['detail']}" + (f"\n     -> {r['fix']}" if r["fix"] else ""))
            return 0 if all(r["ok"] for r in rows if r["check"] in ("python", "ffmpeg+ffprobe", "libass (captions)")) else 1
        elif args.command == "run":
            from .pipeline import run_cycle
            _print(run_cycle(not args.no_discover, args.per_category, args.execute, args.publish, args.share))
        return 0
    except (RuntimeError, ValueError, OSError, json.JSONDecodeError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
