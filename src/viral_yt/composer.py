"""Render an approved ORIGINAL explainer brief: voice-over + animated captions + hook card over
your own / licensed b-roll, or a generated branded motion background when no visuals are given."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .captions import build_ass
from .common import ROOT, is_true, local_file, settings
from .ffmpeg_tools import check_ffmpeg, encode_args, layout_chain, loudnorm, probe_duration, probe_hw_encoder, run
from .originality import score as originality
from .scriptgen import check_ready, load_brief, script_text
from .transcripts import words_from_cues, write_srt
from .tts import synthesize

OUT = ROOT / "output" / "shorts"


def _voice(brief: dict, oid: str) -> tuple[Path, str]:
    own = (brief.get("voice_file") or "").strip()
    if own:
        path = local_file(own)
        if not path.is_file():
            raise RuntimeError(f"voice_file not found: {path}")
        return path, "own"
    path, engine = synthesize(script_text(brief), ROOT / "media" / "voice" / f"{oid}.wav")
    if engine == "voicestudio":  # neural / cloned voices are realistic -> disclose (YouTube containsSyntheticMedia)
        brief["contains_realistic_synthetic_media"] = True
    return path, "tts"


def _word_timings(voice: Path, text: str, duration: float):
    try:  # accurate word timings if faster-whisper is installed
        from faster_whisper import WhisperModel  # noqa: F401
        from .transcripts import transcribe_owned_file
        srt = transcribe_owned_file(voice, voice.with_suffix(".srt"), model_size="base")
        words = json.loads(srt.with_suffix(".words.json").read_text(encoding="utf-8"))
        if words:
            return [tuple(w) for w in words]
    except Exception:
        pass
    # Fallback: spread the script proportionally over the voice duration (good enough for TTS pacing)
    return words_from_cues([(0.15, max(duration - 0.2, 0.5), text)])


def compose(brief_ref: str, preset: str | None = None, published_titles: list[str] | None = None,
            force: bool = False) -> Path:
    cfg = settings()["render"]
    w, h, fps = cfg["width"], cfg["height"], cfg["fps"]
    path, brief = load_brief(brief_ref)
    problems = check_ready(brief)
    if problems and not force:
        raise RuntimeError("Brief not ready: " + "; ".join(problems))
    check_ffmpeg()
    oid = brief["brief_id"]
    OUT.mkdir(parents=True, exist_ok=True)
    voice, voice_kind = _voice(brief, oid)
    vdur = probe_duration(voice)
    dur = round(vdur + 0.7, 3)
    if not 6 <= dur <= 180:
        raise RuntimeError(f"Voice-over is {vdur:.1f}s; Shorts must be 6-180 s (aim 35-58 s)")
    text = script_text(brief)
    words = _word_timings(voice, text, vdur)
    ass = OUT / f"{oid}.ass"
    brand = settings()["brand"]["name"].upper()
    build_ass(words, ass, w, h, cfg["font"], cfg["caption_size"], brief["script"].get("hook", ""),
              cfg["hook_seconds"], f"{brand} • {str(brief.get('category','')).upper()}", dur, cfg["max_words_per_caption"])
    write_srt(words, OUT / f"{oid}.srt")
    visuals = [local_file(v) for v in brief.get("visuals", []) if str(v).strip()]
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    if visuals:
        for v in visuals:
            cmd += ["-stream_loop", "-1", "-i", str(v)]
        seg = dur / len(visuals)
        parts = []
        for k in range(len(visuals)):
            parts.append(layout_chain(f"{k}:v", "blurfit", w, h, fps, f"l{k}") + f";[l{k}]trim=0:{seg:.3f},setpts=PTS-STARTPTS[s{k}]")
        fc = ";".join(parts) + ";" + "".join(f"[s{k}]" for k in range(len(visuals))) + f"concat=n={len(visuals)}:v=1:a=0[bg]"
        vin = len(visuals)
        visuals_kind = "own" if brief.get("visuals_owned") else "licensed"
    else:
        cmd += ["-f", "lavfi", "-i", f"gradients=s={w}x{h}:c0=0x111827:c1=0x0E7490:c2=0x111827:c3=0x9F1239:"
                                      f"nb_colors=4:speed=0.008:r={fps}:d={dur}"]
        fc = f"[0:v]noise=alls=6:allf=t,vignette=PI/5,setsar=1,fps={fps}[bg]"
        vin = 1
        visuals_kind = "generated"
    cmd += ["-i", str(voice)]
    fc += f";[bg]ass={ass.name}[v];[{vin}:a]apad,atrim=0:{dur},{loudnorm(cfg['loudness_lufs'])}[a]"
    out_file = OUT / f"{oid}.mp4"
    cmd += ["-filter_complex", fc, "-map", "[v]", "-map", "[a]", "-t", str(dur),
            *encode_args(cfg["crf"], preset or cfg["preset"], fps), out_file.name]
    enc = None if cfg.get("hardware_encoder", "auto") == "off" else probe_hw_encoder(ROOT / "output" / ".encoder.json")
    if enc:
        cmd[-len(encode_args(cfg["crf"], preset or cfg["preset"], fps)) - 1:-1] = encode_args(cfg["crf"], preset or cfg["preset"], fps, enc)
    try:
        run(cmd, cwd=OUT)
    except RuntimeError:
        if not enc:
            raise
        cmd[-len(encode_args(cfg["crf"], preset or cfg["preset"], fps, enc)) - 1:-1] = encode_args(cfg["crf"], preset or cfg["preset"], fps)
        run(cmd, cwd=OUT)
    desc = brief.get("description", "")
    rp = brief.get("research_pointer") or {}
    meta = {
        "output_id": oid, "asset_id": oid, "brief_file": str(path), "kind": "explainer",
        "rendered_utc": datetime.now(timezone.utc).isoformat(), "category": brief.get("category"),
        "title": brief.get("title"), "description": desc, "tags": brief.get("tags", []),
        "hashtags": brief.get("hashtags", []), "rights_basis": "original", "rights_evidence": brief.get("visual_rights"),
        "research_pointer": rp.get("url", ""), "file": str(out_file), "duration_seconds": dur,
        "captions_included": True, "hook_text": brief["script"].get("hook", ""), "layout": "blurfit",
        "voice": voice_kind, "visuals_kind": visuals_kind,
        "contains_realistic_synthetic_media": is_true(brief.get("contains_realistic_synthetic_media")),
    }
    meta["originality_score"], meta["originality_reasons"] = originality(meta, published_titles)
    (OUT / f"{oid}.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    from .state import record_render
    record_render(oid, oid, "explainer", str(out_file), meta["title"] or "", meta["originality_score"], meta)
    return out_file
