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
from .ffmpeg_tools import (RATIO_SUFFIX, check_ffmpeg, encode_args, ffmpeg_filter_path, has_audio,  # noqa: F401
                           layout_chain, layout_fit, loudnorm, probe_duration, probe_hw_encoder, ratio_dims, run)
from .highlights import audio_energy, select_clips
from .media_prep import align_cut, extract_audio, load_words, sentences, silences, work_dir
from .originality import score as originality
from .reframe import analyze
from .rights import THIRD_PARTY, load_asset, parse_ranges, validate_asset
from .transcripts import words_from_cues, write_srt

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
    count = max(1, min(20, int(asset.get("clip_count") or ccfg["per_source"])))
    key = json.dumps([ccfg["min_seconds"], ccfg["max_seconds"], count, llm_on])
    cache = work_dir(source) / "highlights.json"
    if cache.is_file():
        data = json.loads(cache.read_text())
        if data.get("key") == key:
            return data["clips"], words, {**data["stats"], "cached": True}
    sents = sentences(words)
    energy = audio_energy(source)
    clips, stats = select_clips(sents, count, ccfg["min_seconds"], ccfg["max_seconds"], energy, llm_on)
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
        return (f"{trim},crop={seg['cw']}:{seg['ch']}:{seg['x']}:0,scale={w}:{h}:force_original_aspect_ratio=increase,"
                f"crop={w}:{h},setsar=1,fps={fps}[{label}]")
    return f"{trim}[t{k}];" + layout_fit(f"t{k}", w, h, fps, label, tag=f"f{k}")


def make_hook(plan: dict, max_words: int = 7) -> str:
    """Hook card text: LLM title if present, else the opening line trimmed to a punchy length."""
    if plan.get("title"):
        return plan["title"]
    words = (plan.get("hook_line") or "").replace("\n", " ").split()
    text = " ".join(words[:max_words]).rstrip(",;:")
    return text + ("…" if len(words) > max_words else "")


def _opt(asset: dict, key: str, default):
    v = asset.get(key)
    return default if v in (None, "") else v


def render_clip(asset: dict, source: Path, plan: dict, oid: str, words_all, *, ratio: str = "9:16",
                out_dir: Path | None = None, enc: str | None = None, preset: str | None = None,
                override: dict | None = None, preview: bool = False, part: tuple[int, int] = (1, 1)) -> dict:
    """Render ONE clip in one FFmpeg pass. `override` (from the review editor) may set:
    start/end (absolute source seconds), captions [{start,end,text}] on the OUTPUT timeline, hook, caption_style,
    cold_open. preview=True -> half-size, no burned captions, ultrafast (for the browser editor)."""
    cfg = settings()["render"]
    override = override or {}
    out_dir = out_dir or OUT
    out_dir.mkdir(parents=True, exist_ok=True)
    preset = "ultrafast" if preview else (preset or cfg["preset"])
    w, h = ratio_dims(ratio)
    if preview:
        w, h = w // 2 - (w // 2) % 2, h // 2 - (h // 2) % 2
    fps = cfg["fps"]
    basis = asset["rights_basis"].strip().lower()
    commentary = (asset.get("commentary_audio") or "").strip()
    layout = str(_opt(asset, "layout", cfg.get("clip_layout", "smart"))).strip()
    mix = str(_opt(asset, "mix_mode", "duck" if basis in THIRD_PARTY else "source")).strip()
    style = str(override.get("caption_style") or _opt(asset, "caption_style", cfg.get("caption_style", "bold_pop")))
    cold = is_true(override["cold_open"]) if "cold_open" in override else is_true(_opt(asset, "cold_open", cfg.get("cold_open", True)))
    a, b = float(override.get("start", plan["start"])), float(override.get("end", plan["end"]))
    if not 0 <= a < b or b - a > 180:
        raise ValueError("trim must give 0 <= start < end and at most 180 s")
    dur = b - a
    wd = work_dir(source)
    rw, rh = (int(x) for x in ratio.split(":"))
    if layout == "smart":
        wav = extract_audio(source) if has_audio(source) else None
        segs = analyze(source, a, dur, wd, rw / rh, wav)
    else:
        from .reframe import plan_segments, probe_size
        sw, sh = probe_size(source)
        segs = plan_segments(dur, [], None, sw, sh, sw, rw / rh)
        if layout == "blurfit":
            segs = [{**sg, "mode": "fit"} for sg in segs]

    qspan = plan.get("quote_span") if cold else None
    qd, qa, qb = 0.0, 0.0, 0.0
    if qspan and qspan[1] - qspan[0] >= 1.2 and a <= qspan[0] < b:
        qa, qb = max(a, qspan[0] - 0.05), min(b, qspan[1] + 0.15)
        qd = qb - qa
    total = dur + qd
    if override.get("captions") is not None:
        cues = [(float(c["start"]), float(c["end"]), str(c["text"])[:200]) for c in override["captions"]
                if str(c.get("text", "")).strip() and 0 <= float(c["start"]) < float(c["end"]) <= total + 0.5]
        cap_words = words_from_cues(sorted(cues))
    else:
        clip_words = [(max(s, a) - a + qd, min(e, b) - a + qd, t) for s, e, t in words_all if e > a and s < b]
        cap_words = ([(s - qa, e - qa, t) for s, e, t in words_all if e > qa and s < qb] if qd else []) + clip_words
    hook = str(override.get("hook", asset.get("hook_text") or make_hook(plan))).strip()[:120]
    credit = (f"Footage: {asset.get('owner')}" + (" (CC BY)" if basis == "cc_by" else "")) if basis in THIRD_PARTY else ""
    out_name = f"{oid}.preview.mp4" if preview else f"{oid}.mp4"
    ass = out_dir / f"{oid}.ass"
    if not preview:
        build_ass(cap_words, ass, w, h, cfg["font"], None, hook, cfg["hook_seconds"], credit, total, None, style=style)
        write_srt(cap_words, out_dir / f"{oid}.srt") if cap_words else None

    src_audio = has_audio(source)
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{a:.3f}", "-t", f"{dur:.3f}", "-i", str(source)]
    vin, nxt = {}, 1
    if qd:
        cmd += ["-ss", f"{qa:.3f}", "-t", f"{qd:.3f}", "-i", str(source)]
        vin["quote"] = nxt; nxt += 1
    if commentary:
        cmd += ["-i", str(local_file(commentary))]
        vin["vo"] = nxt; nxt += 1
    parts, labels = [f"[0:v]split={len(segs)}" + "".join(f"[in{k}]" for k in range(len(segs)))], []
    if qd:
        qrel = qa - a
        qseg = next((sg for sg in segs if sg["start"] <= qrel < sg["end"]), segs[0])
        parts.append(_segment_chain({**qseg, "start": 0, "end": qd}, f"{vin['quote']}:v", "vq", w, h, fps, 99))
        labels.append("[vq]")
    for k, seg in enumerate(segs):
        parts.append(_segment_chain(seg, f"in{k}", f"v{k}", w, h, fps, k))
        labels.append(f"[v{k}]")
    tail = "[v]" if preview else f"[vc];[vc]ass={ass.name}[v]"
    parts.append("".join(labels) + f"concat=n={len(labels)}:v=1:a=0" + tail)
    lufs = loudnorm(cfg["loudness_lufs"])
    if src_audio:
        parts.append(f"[{vin['quote']}:a][0:a]concat=n=2:v=0:a=1[srcA]" if qd else "[0:a]anull[srcA]")
    if commentary and src_audio and mix == "duck":
        parts.append(f"[srcA]volume=0.22[bed];[{vin['vo']}:a]apad,atrim=0:{total:.3f}[vo];"
                     f"[bed][vo]amix=inputs=2:duration=first:normalize=0,{lufs}[a]")
    elif commentary:
        parts.append(f"[{vin['vo']}:a]apad,atrim=0:{total:.3f},{lufs}[a]")
    elif src_audio:
        parts.append(f"[srcA]{lufs}[a]" if not preview else "[srcA]anull[a]")
    else:
        parts.append(f"anullsrc=r=48000:cl=stereo,atrim=0:{total:.3f}[a]")
    base_cmd = cmd + ["-filter_complex", ";".join(parts), "-map", "[v]", "-map", "[a]", "-t", f"{total:.3f}"]
    used = None if preview else enc
    try:
        run(base_cmd + encode_args(cfg["crf"] + (6 if preview else 0), preset, fps, used) + [out_name], cwd=out_dir)
    except RuntimeError:
        if not used:
            raise
        used = None
        run(base_cmd + encode_args(cfg["crf"], preset, fps, None) + [out_name], cwd=out_dir)
    out_file = out_dir / out_name
    if preview:
        return {"file": str(out_file), "duration_seconds": round(total, 2)}
    n, total_parts = part
    title = (asset.get("title") or "").strip()
    title = (f"{title} (Part {n})" if total_parts > 1 else title) if title else (plan.get("title") or hook)
    return {
        "output_id": oid, "asset_id": asset["asset_id"], "kind": "clip", "rendered_utc": datetime.now(timezone.utc).isoformat(),
        "category": asset.get("category"), "title": title[:100],
        "description": asset.get("description", ""), "tags": [t.strip() for t in (asset.get("tags") or "").split("|") if t.strip()],
        "rights_basis": basis, "rights_evidence": asset.get("rights_evidence"),
        "source_video_url": asset.get("source_video_url", ""), "source_owner": asset.get("owner", ""),
        "source_file": str(source), "file": str(out_file), "ratio": ratio, "duration_seconds": round(total, 2),
        "range": [a, b], "plan": plan, "cold_open_seconds": round(qd, 2),
        "captions_included": bool(cap_words), "caption_style": style, "hook_text": hook, "layout": layout,
        "reframe_segments": segs, "encoder": used or "libx264", "voice": "own" if commentary else None,
        "source_audio": "ducked" if (commentary and mix == "duck") else ("none" if commentary else "full"),
        "contains_realistic_synthetic_media": False, "asset": asset, "edited": bool(override),
        "highlight": {k: plan.get(k) for k in ("score", "source", "reason", "hook_line")},
    }


def finalize(meta: dict, out_dir: Path, published_titles: list[str] | None = None, record: bool = True) -> dict:
    meta["originality_score"], meta["originality_reasons"] = originality(meta, published_titles)
    (out_dir / f"{meta['output_id']}.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    if record:
        from .state import record_render
        record_render(meta["output_id"], meta["asset_id"], "clip", meta["file"], meta["title"], meta["originality_score"], meta)
    return meta


def render_asset(asset: dict, source: Path, *, ratios: list[str] | None = None, out_dir: Path | None = None,
                 preset: str | None = None, published_titles: list[str] | None = None, record: bool = True,
                 progress=None) -> list[dict]:
    """Plan + render every highlight of one source in every requested ratio (parallel)."""
    check_ffmpeg()
    out_dir = out_dir or OUT
    out_dir.mkdir(parents=True, exist_ok=True)
    start = float(asset.get("clip_start") or 0)
    end = float(asset.get("clip_end") or 0)
    actual = probe_duration(source)
    if progress:
        progress("selecting highlights")
    plans, words, stats = plan_clips(asset, source, start, end)
    if not plans:
        raise RuntimeError("No highlight met the length/quality bar; widen clips.min/max_seconds or add clip_ranges")
    for p in plans:
        p["end"] = min(float(p["end"]), actual)
    plans = [p for p in plans if float(p["end"]) - float(p["start"]) >= 3]   # transcript longer than media, etc.
    if not plans:
        raise RuntimeError("No highlight fits inside the media duration")
    ratios = ratios or [str(asset.get("ratio") or settings()["render"].get("ratio", "9:16"))]
    for r in ratios:
        ratio_dims(r)
    enc = _encoder()
    jobs = []
    for n, plan in enumerate(plans, 1):
        base = safe_name(asset["asset_id"] if len(plans) == 1 else f"{asset['asset_id']}_c{n}")
        for r in ratios:
            jobs.append((plan, base + RATIO_SUFFIX[r], r, n))
    if progress:
        progress(f"rendering {len(jobs)} clips")
    workers = max(1, int(settings()["clips"].get("workers", 2)))
    with ThreadPoolExecutor(max_workers=min(workers, len(jobs))) as pool:
        metas = list(pool.map(lambda j: render_clip(asset, source, j[0], j[1], words, ratio=j[2], out_dir=out_dir,
                                                    enc=enc, preset=preset, part=(j[3], len(plans))), jobs))
    out = []
    for meta in metas:
        meta["selection"] = stats
        out.append(finalize(meta, out_dir, published_titles, record))
    log.info("render_asset %s: %d outputs, selection=%s, encoder=%s", asset["asset_id"], len(out), stats, enc)
    return out


def rerender(output_id: str, override: dict, out_dir: Path | None = None, preview: bool = False,
             record: bool = True) -> dict:
    """Re-render one clip with review-editor edits (trim, captions, hook, style, cold open)."""
    out_dir = out_dir or OUT
    meta = json.loads((out_dir / f"{safe_name(output_id)}.json").read_text(encoding="utf-8"))
    asset, source = meta["asset"], Path(meta["source_file"])
    words = load_words(asset, source, allow_asr=False)
    enc = None if preview else _encoder()
    if preview:
        return render_clip(asset, source, meta["plan"], meta["output_id"], words, ratio=meta.get("ratio", "9:16"),
                           out_dir=out_dir, override={k: v for k, v in override.items() if k in ("start", "end", "cold_open")},
                           preview=True)
    new = render_clip(asset, source, meta["plan"], meta["output_id"], words, ratio=meta.get("ratio", "9:16"),
                      out_dir=out_dir, enc=enc, override=override)
    new["selection"] = meta.get("selection")
    if override.get("title"):
        new["title"] = str(override["title"])[:100]
    return finalize(new, out_dir, None, record)


def render(asset_id: str, preset: str | None = None) -> Path:
    """Back-compat: render and return the first output."""
    return render_all(asset_id, preset)[0]


def render_all(asset_id: str, preset: str | None = None, published_titles: list[str] | None = None,
               ratios: list[str] | None = None) -> list[Path]:
    asset = load_asset(asset_id)
    source, _, _ = validate_asset(asset)
    if ratios is None and asset.get("ratios"):
        ratios = [r.strip() for r in str(asset["ratios"]).split("|") if r.strip()]
    metas = render_asset(asset, source, ratios=ratios, preset=preset, published_titles=published_titles)
    return [Path(m["file"]) for m in metas]


def timeline_captions(meta: dict, start: float, end: float, cold_open: bool, group: int = 4) -> tuple[list[dict], float]:
    """Caption lines (output timeline) for a prospective trim — used by the review editor after re-trimming."""
    from .captions import group_words
    asset, source, plan = meta["asset"], Path(meta["source_file"]), meta["plan"]
    words_all = load_words(asset, source, allow_asr=False)
    qspan = plan.get("quote_span") if cold_open else None
    qd = qa = qb = 0.0
    if qspan and qspan[1] - qspan[0] >= 1.2 and start <= qspan[0] < end:
        qa, qb = max(start, qspan[0] - 0.05), min(end, qspan[1] + 0.15)
        qd = qb - qa
    words = ([(s - qa, e - qa, t) for s, e, t in words_all if e > qa and s < qb] if qd else []) + \
            [(max(s, start) - start + qd, min(e, end) - start + qd, t) for s, e, t in words_all if e > start and s < end]
    return ([{"start": round(g[0][0], 2), "end": round(g[-1][1], 2), "text": " ".join(w[2] for w in g)}
             for g in group_words(words, group)], round(qd, 2))
