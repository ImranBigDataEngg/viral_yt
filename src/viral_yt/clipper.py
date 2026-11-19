"""Clip engine: render PERMISSIONED long-form sources (ledger-approved) into vertical Shorts.

Stages (each cached under output/work/<fingerprint>/ so re-runs resume):
  words (own SRT/words.json or local faster-whisper) -> sentences -> silences
  -> hybrid highlight selection (local heuristic shortlist, optional LLM judge)
  -> silence-aligned cut points -> shot-aware smart reframe (face crop / blur-fit per shot)
  -> optional cold open (best quote first) -> styled captions + hook + credit
  -> voice-over ducking + EBU R128 loudness -> ONE FFmpeg pass per clip (GPU encoder if it works)
Clips render in parallel (settings.clips.workers).
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from .captions import build_ass
from .common import ROOT, get_logger, is_true, local_file, safe_name, settings
from .ffmpeg_tools import (check_ffmpeg, encode_args, ffmpeg_filter_path, has_audio, layout_chain,  # noqa: F401
                           layout_fit, loudnorm, probe_duration, probe_hw_encoder, run)
from .highlights import audio_energy, select_clips
from .media_prep import align_cut, load_words, sentences, silences, work_dir
from .originality import score as originality
from .reframe import analyze
from .rights import THIRD_PARTY, load_asset, parse_ranges, validate_asset
from .transcripts import write_srt

OUT = ROOT / "output" / "shorts"
log = get_logger("clipper")


def _encoder() -> str | None:
    if settings()["render"].get("hardware_encoder", "auto") == "off":
        return None
    return probe_hw_encoder(ROOT / "output" / ".encoder.json")


def plan_clips(asset: dict, source: Path, start: float, end: float) -> tuple[list[dict], list, dict]:
    """Return (plans, words, stats). Cached per source + settings."""
    cfg = settings()
    ccfg, llm_on = cfg["clips"], cfg["llm"].get("engine", "none") != "none" and cfg["clips"].get("llm_rerank", True)
    words = load_words(asset, source, allow_asr=(asset.get("clip_ranges") or "").strip().lower() == "auto")
    raw = (asset.get("clip_ranges") or "").strip()
    if raw.lower() != "auto":
        ranges = parse_ranges(raw) or [(start, end)]
        return [{"start": a, "end": b, "hook_line": "", "title": "", "quote": None, "source": "manual"} for a, b in ranges], words, {"mode": "manual"}
    key = json.dumps([ccfg["min_seconds"], ccfg["max_seconds"], ccfg["per_source"], llm_on])
    cache = work_dir(source) / "highlights.json"
    if cache.is_file():
        data = json.loads(cache.read_text())
        if data.get("key") == key:
            return data["clips"], words, {**data["stats"], "cached": True}
    sents = sentences(words)
    energy = audio_energy(source)
    clips, stats = select_clips(sents, int(ccfg["per_source"]), ccfg["min_seconds"], ccfg["max_seconds"], energy, llm_on)
    sil = silences(source)
    for c in clips:                                     # cut inside measured silences, not at fixed pads
        c["start"], c["end"] = round(align_cut(c["start"], sil, "start"), 3), round(align_cut(c["end"], sil, "end"), 3)
        q = c.get("quote")
        c["quote_span"] = [sents[q][0], sents[q][1]] if q is not None else None
    cache.write_text(json.dumps({"key": key, "clips": clips, "stats": stats}, indent=2))
    return clips, words, stats


def _segment_chain(seg: dict, src: str, label: str, w: int, h: int, fps: int, k: int) -> str:
    trim = f"[{src}]trim=start={seg['start']:.3f}:end={seg['end']:.3f},setpts=PTS-STARTPTS"
    if seg["mode"] == "crop":
        return f"{trim},crop={seg['cw']}:{seg['ch']}:{seg['x']}:0,scale={w}:{h},setsar=1,fps={fps}[{label}]"
    return f"{trim}[t{k}];" + layout_fit(f"t{k}", w, h, fps, label, tag=f"f{k}")


def _render_one(asset: dict, source: Path, plan: dict, n: int, total_plans: int, words_all, enc: str | None,
                preset: str | None = None) -> dict:
    cfg = settings()["render"]
    preset = preset or cfg["preset"]
    w, h, fps = cfg["width"], cfg["height"], cfg["fps"]
    basis = asset["rights_basis"].strip().lower()
    commentary = (asset.get("commentary_audio") or "").strip()
    layout = (asset.get("layout") or cfg.get("clip_layout", "smart")).strip()
    mix = (asset.get("mix_mode") or ("duck" if basis in THIRD_PARTY else "source")).strip()
    style = (asset.get("caption_style") or cfg.get("caption_style", "bold_pop")).strip()
    cold = is_true(asset.get("cold_open") if asset.get("cold_open") not in (None, "") else cfg.get("cold_open", True))
    a, b = float(plan["start"]), float(plan["end"])
    dur = b - a
    oid = safe_name(asset["asset_id"] if total_plans == 1 else f"{asset['asset_id']}_c{n}")
    wd = work_dir(source)

    if layout == "smart":
        segs = analyze(source, a, dur, wd)
    else:
        from .reframe import probe_size
        sw, sh = probe_size(source)
        cw = min(sw, int(round(sh * 9 / 16 / 2)) * 2)
        segs = [{"start": 0.0, "end": round(dur, 3), "mode": "crop" if layout == "crop" else "fit",
                 "x": max(0, (sw - cw) // 2), "cw": cw, "ch": sh}]

    qspan = plan.get("quote_span") if cold else None
    qd = 0.0
    if qspan and qspan[1] - qspan[0] >= 1.2:
        qa, qb = max(a, qspan[0] - 0.05), min(b, qspan[1] + 0.15)
        qd = qb - qa
    clip_words = [(max(s, a) - a + qd, min(e, b) - a + qd, t) for s, e, t in words_all if e > a and s < b]
    cap_words = ([(s - qa, e - qa, t) for s, e, t in words_all if e > qa and s < qb] if qd else []) + clip_words
    total = dur + qd
    hook = (asset.get("hook_text") or plan.get("title") or "").strip()
    credit = (f"Footage: {asset.get('owner')}" + (" (CC BY)" if basis == "cc_by" else "")) if basis in THIRD_PARTY else ""
    ass = OUT / f"{oid}.ass"
    build_ass(cap_words, ass, w, h, cfg["font"], None, hook, cfg["hook_seconds"], credit, total, None, style=style)
    if cap_words:
        write_srt(cap_words, OUT / f"{oid}.srt")

    src_audio = has_audio(source)
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{a:.3f}", "-t", f"{dur:.3f}", "-i", str(source)]
    vin = {"clip": 0}
    nxt = 1
    if qd:
        cmd += ["-ss", f"{qa:.3f}", "-t", f"{qd:.3f}", "-i", str(source)]
        vin["quote"] = nxt; nxt += 1
    if commentary:
        cmd += ["-i", str(local_file(commentary))]
        vin["vo"] = nxt; nxt += 1
    parts, labels = [], []
    parts.append(f"[0:v]split={len(segs)}" + "".join(f"[in{k}]" for k in range(len(segs))))
    if qd:  # frame the cold open like the shot it comes from
        qrel = qa - a
        qseg = next((s for s in segs if s["start"] <= qrel < s["end"]), segs[0])
        parts.append(_segment_chain({**qseg, "start": 0, "end": qd}, f"{vin['quote']}:v", "vq", w, h, fps, 99))
        labels.append("[vq]")
    for k, seg in enumerate(segs):
        parts.append(_segment_chain(seg, f"in{k}", f"v{k}", w, h, fps, k))
        labels.append(f"[v{k}]")
    parts.append("".join(labels) + f"concat=n={len(labels)}:v=1:a=0[vc];[vc]ass={ass.name}[v]")
    lufs = loudnorm(cfg["loudness_lufs"])
    if src_audio:
        base = f"[{vin['quote']}:a][0:a]concat=n=2:v=0:a=1[srcA]" if qd else "[0:a]anull[srcA]"
        parts.append(base)
    if commentary and src_audio and mix == "duck":
        parts.append(f"[srcA]volume=0.22[bed];[{vin['vo']}:a]apad,atrim=0:{total:.3f}[vo];"
                     f"[bed][vo]amix=inputs=2:duration=first:normalize=0,{lufs}[a]")
    elif commentary:
        parts.append(f"[{vin['vo']}:a]apad,atrim=0:{total:.3f},{lufs}[a]")
    elif src_audio:
        parts.append(f"[srcA]{lufs}[a]")
    else:
        parts.append(f"anullsrc=r=48000:cl=stereo,atrim=0:{total:.3f}[a]")
    out_file = OUT / f"{oid}.mp4"
    base_cmd = cmd + ["-filter_complex", ";".join(parts), "-map", "[v]", "-map", "[a]", "-t", f"{total:.3f}"]
    used = enc
    try:
        run(base_cmd + encode_args(cfg["crf"], preset, fps, enc) + [out_file.name], cwd=OUT)
    except RuntimeError:
        if not enc:
            raise
        used = None                                   # GPU encoder failed mid-render -> CPU, same quality target
        run(base_cmd + encode_args(cfg["crf"], preset, fps, None) + [out_file.name], cwd=OUT)
    meta = {
        "output_id": oid, "asset_id": asset["asset_id"], "kind": "clip", "rendered_utc": datetime.now(timezone.utc).isoformat(),
        "category": asset.get("category"),
        "title": (asset.get("title") or plan.get("title") or "").strip() + (f" (Part {n})" if total_plans > 1 and asset.get("title") else ""),
        "description": asset.get("description", ""), "tags": [t.strip() for t in (asset.get("tags") or "").split("|") if t.strip()],
        "rights_basis": basis, "rights_evidence": asset.get("rights_evidence"),
        "source_video_url": asset.get("source_video_url", ""), "source_owner": asset.get("owner", ""),
        "file": str(out_file), "duration_seconds": round(total, 2), "range": [a, b], "cold_open_seconds": round(qd, 2),
        "captions_included": bool(cap_words), "caption_style": style, "hook_text": hook, "layout": layout,
        "reframe_segments": segs, "encoder": used or "libx264",
        "voice": "own" if commentary else None,
        "source_audio": "ducked" if (commentary and mix == "duck") else ("none" if commentary else "full"),
        "contains_realistic_synthetic_media": False, "highlight": {k: plan.get(k) for k in ("score", "source", "reason", "hook_line")},
    }
    return meta


def render(asset_id: str, preset: str | None = None) -> Path:
    """Back-compat: render and return the first output."""
    return render_all(asset_id, preset)[0]


def render_all(asset_id: str, preset: str | None = None, published_titles: list[str] | None = None) -> list[Path]:
    asset = load_asset(asset_id)
    source, start, end = validate_asset(asset)
    check_ffmpeg()
    actual = probe_duration(source)
    OUT.mkdir(parents=True, exist_ok=True)
    plans, words, stats = plan_clips(asset, source, start, end)
    if not plans:
        raise RuntimeError("No highlight met the length/quality bar; widen clips.min/max_seconds or add clip_ranges")
    for p in plans:
        if float(p["end"]) > actual + 0.25:
            p["end"] = actual
    enc = _encoder()
    workers = max(1, int(settings()["clips"].get("workers", 2)))
    with ThreadPoolExecutor(max_workers=min(workers, len(plans))) as pool:
        metas = list(pool.map(lambda np: _render_one(asset, source, np[1], np[0], len(plans), words, enc, preset),
                              enumerate(plans, 1)))
    from .state import record_render
    outputs = []
    for meta in metas:
        meta["selection"] = stats
        meta["originality_score"], meta["originality_reasons"] = originality(meta, published_titles)
        (OUT / f"{meta['output_id']}.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        record_render(meta["output_id"], asset_id, "clip", meta["file"], meta["title"], meta["originality_score"], meta)
        outputs.append(Path(meta["file"]))
    log.info("render_all %s: %d clips, selection=%s, encoder=%s", asset_id, len(outputs), stats, enc)
    return outputs
