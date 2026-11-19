"""Render PERMISSIONED long-form sources (ledger-approved) into one or more vertical Shorts.

Per output: chosen range -> 9:16 layout (blurfit/crop) -> animated captions + hook card + credit ->
your commentary mixed over (source ducked) -> loudness-normalised MP4 + JSON sidecar.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .captions import build_ass
from .common import ROOT, local_file, safe_name, settings
from .ffmpeg_tools import (check_ffmpeg, encode_args, ffmpeg_filter_path, has_audio, layout_chain,  # noqa: F401
                           loudnorm, probe_duration, run)
from .highlights import audio_energy, find_highlights
from .originality import score as originality
from .rights import THIRD_PARTY, load_asset, parse_ranges, validate_asset
from .transcripts import parse_srt, slice_cues, words_from_cues, write_srt

OUT = ROOT / "output" / "shorts"


def plan_ranges(asset: dict, source: Path, start: float, end: float) -> list[dict]:
    cfg = settings()["clips"]
    raw = (asset.get("clip_ranges") or "").strip()
    if raw.lower() == "auto":
        cues = parse_srt(local_file(asset["transcript_file"]).read_text(encoding="utf-8-sig"))
        return find_highlights(cues, cfg["min_seconds"], cfg["max_seconds"], int(cfg["per_source"]), audio_energy(source))
    ranges = parse_ranges(raw) or [(start, end)]
    return [{"start": a, "end": b, "hook_line": ""} for a, b in ranges]


def _words_for(asset: dict, start: float, end: float):
    tf = (asset.get("transcript_file") or "").strip()
    if not tf:
        return []
    path = local_file(tf)
    wj = path.with_suffix(".words.json")
    if wj.is_file():
        words = json.loads(wj.read_text(encoding="utf-8"))
        return [(max(a, start) - start, min(b, end) - start, w) for a, b, w in words if b > start and a < end]
    return words_from_cues(slice_cues(parse_srt(path.read_text(encoding="utf-8-sig")), start, end))


def render(asset_id: str, preset: str | None = None) -> Path:
    """Back-compat: render and return the first output."""
    return render_all(asset_id, preset)[0]


def render_all(asset_id: str, preset: str | None = None, published_titles: list[str] | None = None) -> list[Path]:
    cfg = settings()["render"]
    w, h, fps = cfg["width"], cfg["height"], cfg["fps"]
    preset = preset or cfg["preset"]
    asset = load_asset(asset_id)
    source, start, end = validate_asset(asset)
    check_ffmpeg()
    actual = probe_duration(source)
    basis = asset["rights_basis"].strip().lower()
    commentary = (asset.get("commentary_audio") or "").strip()
    layout = (asset.get("layout") or cfg["layout"]).strip()
    mix = (asset.get("mix_mode") or ("duck" if basis in THIRD_PARTY else "source")).strip()
    OUT.mkdir(parents=True, exist_ok=True)
    outputs = []
    plans = plan_ranges(asset, source, start, end)
    for n, plan in enumerate(plans, 1):
        a, b = float(plan["start"]), float(plan["end"])
        if b > actual + 0.25:
            raise RuntimeError(f"Clip end {b}s exceeds source duration {actual:.2f}s")
        oid = safe_name(asset_id if len(plans) == 1 else f"{asset_id}_c{n}")
        dur = b - a
        hook = (asset.get("hook_text") or "").strip()
        credit = f"Footage: {asset.get('owner')}" + (" (CC BY)" if basis == "cc_by" else "") if basis in THIRD_PARTY else ""
        words = _words_for(asset, a, b)
        ass = OUT / f"{oid}.ass"
        build_ass(words, ass, w, h, cfg["font"], cfg["caption_size"], hook, cfg["hook_seconds"], credit, dur,
                  cfg["max_words_per_caption"])
        if words:
            write_srt([(x, y, t) for x, y, t in words], OUT / f"{oid}.srt")
        src_audio = has_audio(source)
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{a:.3f}", "-t", f"{dur:.3f}", "-i", str(source)]
        if commentary:
            cmd += ["-i", str(local_file(commentary))]
        fc = layout_chain("0:v", layout, w, h, fps, "lv") + f";[lv]ass={ass.name}[v]"
        lufs = loudnorm(cfg["loudness_lufs"])
        if commentary and src_audio and mix == "duck":
            fc += (f";[0:a]volume=0.22[bed];[1:a]apad,atrim=0:{dur:.3f}[vo];"
                   f"[bed][vo]amix=inputs=2:duration=first:normalize=0,{lufs}[a]")
        elif commentary:
            fc += f";[1:a]apad,atrim=0:{dur:.3f},{lufs}[a]"
        elif src_audio:
            fc += f";[0:a]{lufs}[a]"
        else:
            fc += f";anullsrc=r=48000:cl=stereo,atrim=0:{dur:.3f}[a]"
        out_file = OUT / f"{oid}.mp4"
        cmd += ["-filter_complex", fc, "-map", "[v]", "-map", "[a]", "-t", f"{dur:.3f}",
                *encode_args(cfg["crf"], preset, fps), out_file.name]
        run(cmd, cwd=OUT)  # cwd trick: ASS path has no drive colon -> no Windows escaping issues
        meta = {
            "output_id": oid, "asset_id": asset_id, "kind": "clip", "rendered_utc": datetime.now(timezone.utc).isoformat(),
            "category": asset.get("category"), "title": (asset.get("title") or "").strip() + (f" (Part {n})" if len(plans) > 1 else ""),
            "description": asset.get("description", ""), "tags": [t.strip() for t in (asset.get("tags") or "").split("|") if t.strip()],
            "rights_basis": basis, "rights_evidence": asset.get("rights_evidence"),
            "source_video_url": asset.get("source_video_url", ""), "source_owner": asset.get("owner", ""),
            "file": str(out_file), "duration_seconds": round(dur, 2), "range": [a, b], "captions_included": bool(words),
            "hook_text": hook, "layout": layout, "voice": "own" if commentary else None,
            "source_audio": "ducked" if (commentary and mix == "duck") else ("none" if commentary else "full"),
            "contains_realistic_synthetic_media": False, "highlight_reason": plan.get("hook_line", ""),
        }
        meta["originality_score"], meta["originality_reasons"] = originality(meta, published_titles)
        (OUT / f"{oid}.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        from .state import record_render
        record_render(oid, asset_id, "clip", str(out_file), meta["title"], meta["originality_score"], meta)
        outputs.append(out_file)
    return outputs
