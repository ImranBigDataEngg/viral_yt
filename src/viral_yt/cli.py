"""Command line entry point: python -m viral_yt.cli <command>  (or `trendtide <command>` after pip install -e .)"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .common import ROOT, env, read_csv, settings


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
        if name == "render":
            x.add_argument("--ratios", default="", help="comma list of 9:16,1:1,16:9 (default from ledger/settings)")

    cu = sub.add_parser("clip-url", help="Manual clipper: long video link/file -> N viral clips (rights-checked)")
    cu.add_argument("source", help="YouTube link (own verified channel or CC BY), finished live stream, https video link, or local file")
    cu.add_argument("--count", type=int, default=5); cu.add_argument("--ratios", default="9:16")
    cu.add_argument("--style", default="bold_pop"); cu.add_argument("--no-cold-open", action="store_true")
    cu.add_argument("--basis", default="", help="rights basis for files/direct links: original|permission|licensed|cc_by|public_domain")
    cu.add_argument("--evidence", default="", help="rights evidence (required with --basis)")
    cu.add_argument("--max-minutes", type=int, default=240)

    sub.add_parser("verify-channel", help="Verify which YouTube channels you own (Google OAuth, read-only)")
    w = sub.add_parser("web", help="Run the web app (TT_DEV=1 for local development)")
    w.add_argument("--host", default="127.0.0.1"); w.add_argument("--port", type=int, default=8000)
    sub.add_parser("worker", help="Run web background workers without the web server")
    dg = sub.add_parser("dev-grant", help="LOCAL TESTING ONLY (TT_DEV=1): give an account an active plan without Stripe")
    dg.add_argument("--email", required=True); dg.add_argument("--plan", default="monthly", choices=["daily", "monthly", "yearly"])
    dg.add_argument("--days", type=int, default=30)
    sub.add_parser("stripe-setup", help="Create the TrendTide product + $2/day, $25/month, $250/year prices in Stripe")

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
            ratios = [r.strip() for r in args.ratios.split(",") if r.strip()] or None
            _print({"rendered": [str(x) for x in render_all(args.asset_id, published_titles=published_titles(), ratios=ratios)]})
        elif args.command == "clip-url":
            import uuid
            from .linkjob import run_link_job
            spec = {"job_id": "link_" + uuid.uuid4().hex[:8], "count": args.count, "caption_style": args.style,
                    "ratios": [r.strip() for r in args.ratios.split(",") if r.strip()], "cold_open": not args.no_cold_open,
                    "rights_basis": args.basis, "rights_evidence": args.evidence, "max_seconds": args.max_minutes * 60}
            if Path(args.source).is_file():
                spec["upload_path"] = str(Path(args.source).resolve())
            else:
                spec["url"] = args.source
            channels = set(settings().get("youtube", {}).get("my_channel_ids", []))
            metas = run_link_job(spec, ROOT / "output" / "shorts", channels, lambda m: print("..", m, file=sys.stderr), record=True)
            from .manifest import build_manifest
            build_manifest()
            _print({"clips": [{"file": m["file"], "ratio": m["ratio"], "hook": m["hook_text"], "seconds": m["duration_seconds"]}
                              for m in metas], "gallery": str(ROOT / "output/manifests/index.html")})
        elif args.command == "verify-channel":
            from googleapiclient.discovery import build
            from .publisher import oauth_credentials
            yt = build("youtube", "v3", credentials=oauth_credentials(
                ["https://www.googleapis.com/auth/youtube.readonly"], "readonly_token.json"))
            items = yt.channels().list(part="snippet", mine=True, maxResults=50).execute().get("items", [])
            path = ROOT / "config" / "settings.json"
            cfg = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            cfg.setdefault("youtube", {})["my_channel_ids"] = sorted({c["id"] for c in items} |
                                                                      set(cfg.get("youtube", {}).get("my_channel_ids", [])))
            path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
            _print({"verified": [{"id": c["id"], "title": c["snippet"]["title"]} for c in items]})
        elif args.command == "web":
            import uvicorn
            from .web.app import create_app
            uvicorn.run(create_app(), host=args.host, port=args.port, proxy_headers=True,
                        forwarded_allow_ips=env("TT_TRUSTED_PROXIES", "127.0.0.1"))
        elif args.command == "worker":
            import threading
            from .web.config import load
            from .web.worker import loop
            cfg = load()
            loop(cfg.data_dir / "web.db", cfg.data_dir, threading.Event())
        elif args.command == "dev-grant":
            import time
            from .web.config import load
            from .web.db import DB
            cfg = load()
            if not cfg.dev:
                raise RuntimeError("dev-grant only works with TT_DEV=1 (never in production)")
            db = DB(cfg.data_dir / "web.db")
            user = db.one("SELECT id FROM users WHERE email=?", (args.email.strip().lower(),))
            if not user:
                raise RuntimeError("No account with that email - sign up in the browser first")
            db.run("INSERT OR REPLACE INTO subscriptions VALUES (?,?,?,?,?,?)",
                   (user["id"], "sub_devgrant", args.plan, "active", time.time() + args.days * 86400, time.time()))
            _print({"granted": args.plan, "email": args.email, "days": args.days})
        elif args.command == "stripe-setup":
            from .web.billing import stripe
            key = env("STRIPE_SECRET_KEY")
            prod = stripe(key, "POST", "products", {"name": "TrendTide Pro", "description": "AI clipper: long video to viral clips"})
            out = {}
            for plan, (amount, interval) in {"DAILY": (200, "day"), "MONTHLY": (2500, "month"), "YEARLY": (25000, "year")}.items():
                price = stripe(key, "POST", "prices", {"product": prod["id"], "currency": env("TT_CURRENCY", "usd"),
                                                       "unit_amount": amount, "recurring": {"interval": interval},
                                                       "lookup_key": f"trendtide_{plan.lower()}"})
                out[f"STRIPE_PRICE_{plan}"] = price["id"]
            _print({"add_to_.env": out, "mode": "TEST" if key.startswith("sk_test_") else "LIVE"})
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
