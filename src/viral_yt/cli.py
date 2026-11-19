"""Command line entry point: python -m viral_yt.cli <command>."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .clipper import render
from .common import ROOT, local_file
from .discovery import discover
from .manifest import build_manifest
from .publisher import upload
from .rights import load_asset, validate_asset
from .transcripts import parse_srt, select_highlight, transcribe_owned_file


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="TrendTide 60 | Copyright-aware Shorts studio")
    sub = parser.add_subparsers(dest="command", required=True)
    trends = sub.add_parser("discover", help="Research YouTube trends via official API")
    trends.add_argument("--top", type=int, default=10)
    trends.add_argument("--region", default=None)
    trends.add_argument("--days", type=int, default=None)
    trends.add_argument("--topics", type=Path, default=None)
    srt = sub.add_parser("suggest-highlights", help="Rank timestamped captions for editorial review")
    srt.add_argument("--srt", type=Path, required=True)
    for name in ("validate", "render", "transcribe", "upload"):
        command = sub.add_parser(name)
        command.add_argument("--asset-id", required=True)
        if name == "upload":
            command.add_argument("--execute", action="store_true", help="Really upload instead of dry-run")
            command.add_argument("--publish", action="store_true", help="Public upload (requires ledger publish_approved)")
    sub.add_parser("manifest", help="Write CSV of ready-to-download local clips")
    args = parser.parse_args(argv)
    try:
        if args.command == "discover":
            rows = discover(args.top, args.region, args.days, args.topics)
            print(json.dumps({"candidates": len(rows), "csv": str(ROOT / "output/trends/latest_trends.csv"), "per_category": {c: len([x for x in rows if x["category"] == c]) for c in sorted({x["category"] for x in rows})}}, indent=2))
        elif args.command == "suggest-highlights":
            cues = parse_srt(args.srt.read_text(encoding="utf-8-sig"))
            start, end, words = select_highlight(cues)
            print(json.dumps({"start": start, "end": end, "excerpt": words[:500], "editorial_review_required": True}, indent=2))
        elif args.command == "validate":
            record = load_asset(args.asset_id)
            video, start, end = validate_asset(record)
            print(json.dumps({"valid": True, "file": str(video), "clip_start": start, "clip_end": end}, indent=2))
        elif args.command == "render":
            path = render(args.asset_id)
            print(json.dumps({"rendered": str(path), "exists": path.is_file()}, indent=2))
        elif args.command == "transcribe":
            asset = load_asset(args.asset_id)
            path, _, _ = validate_asset(asset)
            out = ROOT / "media/transcripts" / (args.asset_id + ".srt")
            print(json.dumps({"srt": str(transcribe_owned_file(path, out))}))
        elif args.command == "upload":
            print(json.dumps(upload(args.asset_id, args.execute, args.publish), indent=2))
        elif args.command == "manifest":
            file, rows = build_manifest()
            print(json.dumps({"file": str(file), "assets": len(rows)}, indent=2))
        return 0
    except (RuntimeError, ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
