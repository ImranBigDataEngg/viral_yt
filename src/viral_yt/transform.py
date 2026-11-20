"""Clip transformations that ADD real value (never disguise someone else's content):

* make_original(): prepends YOUR take (a short spoken intro: your voice file or TTS of text you wrote/approved)
  over a branded freeze-frame card. Adds genuine commentary; raises the originality score honestly.
* dub(): translates the clip's speech and re-voices it in another language (synthetic voice, disclosed),
  keeping the original audio as a quiet bed and burning translated captions.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .captions import build_ass
from .common import ROOT, get_logger, settings
from .ffmpeg_tools import encode_args, loudnorm, probe_duration, ratio_dims, run
from .transcripts import parse_srt, words_from_cues

log = get_logger("transform")

# language code -> (name, Piper voice id). Voice ids follow rhasspy/piper-voices naming; change in settings.voice.dub
LANGS = {
    "es": ("Spanish", "es_ES-davefx-medium"), "fr": ("French", "fr_FR-siwis-medium"),
    "de": ("German", "de_DE-thorsten-medium"), "pt": ("Portuguese", "pt_BR-faber-medium"),
    "it": ("Italian", "it_IT-riccardo-x_low"), "hi": ("Hindi", "hi_IN-pratham-medium"),
    "ar": ("Arabic", "ar_JO-kareem-medium"), "ru": ("Russian", "ru_RU-dmitri-medium"),
    "tr": ("Turkish", "tr_TR-dfki-medium"), "zh": ("Chinese", "zh_CN-huayan-medium"),
    "en": ("English", "en_US-ryan-high"),
}


class TransformError(RuntimeError):
    pass


def voice_for(lang: str) -> str:
    return settings().get("voice", {}).get("dub", {}).get(lang) or LANGS[lang][1]


def voice_path(voice: str) -> Path:
    return ROOT / "models" / "piper" / f"{voice}.onnx"


def available_languages() -> dict[str, str]:
    """Languages whose voice model is installed (UI shows the rest as 'needs install').
    A running VoiceStudio service (646 languages) makes every language in LANGS available."""
    from . import voicestudio
    if voicestudio.enabled() and voicestudio.health():
        return {k: v[0] for k, v in LANGS.items()}
    return {k: v[0] for k, v in LANGS.items() if voice_path(voice_for(k)).is_file()}


# ------------------------------------------------------------------------------------------- translation
def translate(texts: list[str], lang: str, translator=None) -> list[str]:
    """LLM (if configured) -> Argos Translate (offline) -> error. `translator` injectable for tests."""
    if translator:
        return translator(texts, lang)
    if not texts:
        return []
    cfg = settings()["llm"]
    if cfg.get("engine", "none") != "none":
        from .llm import complete
        prompt = (f"Translate each string to {LANGS[lang][0]} for spoken dubbing of a short video: natural, "
                  "concise, same meaning, no added content. Return ONLY JSON {\"t\": [..]} with exactly "
                  f"{len(texts)} strings in the same order.\n" + json.dumps(texts, ensure_ascii=False))
        for _ in range(2):
            try:
                m = re.search(r"\{.*\}", complete(prompt, cfg), re.S)
                out = json.loads(m.group(0))["t"] if m else []
                if len(out) == len(texts):
                    return [str(x) for x in out]
            except Exception as exc:  # fall through to argos
                log.warning("LLM translation failed: %s", exc)
    try:
        import argostranslate.translate as at
    except ImportError as exc:
        raise TransformError("No translator available: set llm.engine (Ollama/Gemini) or install Argos Translate "
                             "(py scripts\\install.py --voices " + lang + ")") from exc
    return [at.translate(t, "en", lang) for t in texts]


# ------------------------------------------------------------------------------------------- speech
def speak(text: str, out: Path, lang: str, engine: str | None = None) -> Path:
    """VoiceStudio (if configured and up) -> Piper voice for the language. engine='flite' is only for
    tests/English (robotic); engine='voicestudio' or 'piper' forces one engine."""
    out.parent.mkdir(parents=True, exist_ok=True)
    from . import voicestudio
    if engine == "voicestudio" or (engine in (None, "auto") and voicestudio.enabled()):
        try:
            return voicestudio.speak(text, out, lang=lang)
        except voicestudio.VoiceStudioError as exc:
            if engine == "voicestudio":
                raise TransformError(str(exc)) from exc
            log.warning("VoiceStudio failed, falling back to Piper: %s", exc)
    if engine == "flite" or (lang == "en" and not voice_path(voice_for("en")).is_file()):
        from .tts import _flite
        return _flite(text, out, settings()["voice"].get("flite_voice", "slt"))
    model = voice_path(voice_for(lang))
    if not model.is_file():
        raise TransformError(f"{LANGS[lang][0]} voice not installed. Run: py scripts\\install.py --voices {lang}")
    from .tts import _piper
    return _piper(text, out, model)


def _voicestudio_on() -> bool:
    from . import voicestudio
    return voicestudio.enabled()


def _atempo_chain(f: float) -> str:
    f = max(0.5, min(2.0, f))
    return f"atempo={f:.3f}"


def build_dub_track(segments: list[tuple[float, float, str]], total: float, lang: str, workdir: Path,
                    engine: str | None = None) -> Path:
    """segments: (start, slot_end, translated_text) on the clip timeline -> one WAV of `total` seconds.
    Each line is sped up (max 1.6x) to fit its slot; never slowed down (natural pacing)."""
    workdir.mkdir(parents=True, exist_ok=True)
    inputs, chains = [], []
    for i, (s, e, text) in enumerate(segments):
        if not text.strip():
            continue
        wav = speak(text, workdir / f"seg{i:03}.wav", lang, engine)
        dur = probe_duration(wav)
        slot = max(0.3, e - s)
        factor = min(1.6, dur / slot) if dur > slot * 1.02 else 1.0
        k = len(chains)
        inputs += ["-i", str(wav)]
        ms = int(s * 1000)
        chains.append(f"[{k}:a]aresample=48000,{_atempo_chain(factor)},atrim=0:{slot + 0.25:.3f},adelay={ms}|{ms}[d{k}]")
    out = workdir / f"dub_{lang}.wav"
    if not inputs:
        raise TransformError("Nothing to dub: the clip has no captions/speech")
    n = len(chains)
    fc = ";".join(chains) + ";" + "".join(f"[d{k}]" for k in range(n)) + \
        f"amix=inputs={n}:normalize=0:duration=longest,apad,atrim=0:{total:.3f}[a]"
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *inputs, "-filter_complex", fc, "-map", "[a]",
         "-ac", "1", "-ar", "48000", str(out)])
    return out


def _sentences_from_srt(srt: Path) -> list[tuple[float, float, str]]:
    from .media_prep import sentences
    words = [(s, e, t) for s, e, t in parse_srt(srt.read_text(encoding="utf-8"))]
    return [(s[0], s[1], s[2]) for s in sentences(words)]


def dub(output_id: str, lang: str, out_dir: Path, translator=None, engine: str | None = None,
        record: bool = True) -> dict:
    """Create `<output_id>_<lang>`: translated voice + translated captions, same framing/trim as the source clip."""
    if lang not in LANGS:
        raise TransformError(f"Unsupported language '{lang}'")
    out_dir = Path(out_dir).resolve()
    from .clipper import finalize, render_clip
    from .media_prep import load_words
    meta = json.loads((out_dir / f"{output_id}.json").read_text(encoding="utf-8"))
    src_srt = out_dir / f"{output_id}.srt"
    if not src_srt.is_file():
        raise TransformError("This clip has no captions to translate")
    if not Path(meta["source_file"]).is_file():
        raise TransformError("The source video was removed after the retention period; re-upload it to translate.")
    sents = _sentences_from_srt(src_srt)
    texts = [t for _, _, t in sents] + [meta.get("hook_text", "") or ""]
    tr = translate(texts, lang, translator)
    tr_sents, hook = tr[:-1], tr[-1]
    total = float(meta["duration_seconds"])
    segs = []
    for i, ((s, e, _), t) in enumerate(zip(sents, tr_sents)):
        nxt = sents[i + 1][0] if i + 1 < len(sents) else total
        segs.append((s, max(e, min(nxt, e + 0.8)), t))
    workdir = out_dir / f"_{output_id}_{lang}"
    track = build_dub_track(segs, total, lang, workdir, engine)
    new_id = f"{output_id}_{lang}"
    asset, source = meta["asset"], Path(meta["source_file"])
    words = load_words(asset, source, allow_asr=False)
    override = {"start": meta["range"][0], "end": meta["range"][1], "cold_open": meta.get("cold_open_seconds", 0) > 0,
                "captions": [{"start": s, "end": min(e, total), "text": t} for s, e, t in segs],
                "hook": hook or meta.get("hook_text", ""), "dub_audio": str(track),
                "caption_style": meta.get("caption_style")}
    new = render_clip(asset, source, meta["plan"], new_id, words, ratio=meta.get("ratio", "9:16"), out_dir=out_dir,
                      override=override)
    new.update(title=f"{meta.get('title', '')} [{LANGS[lang][0]}]"[:100], language=lang, dubbed_from=output_id,
               contains_realistic_synthetic_media=True, synthetic_voice=True, selection=meta.get("selection"),
               scores=meta.get("scores"), added_commentary=meta.get("added_commentary", False))
    return finalize(new, out_dir, None, record)


# ------------------------------------------------------------------------------------------- make original
def suggest_take(meta: dict) -> str:
    """A starting point for YOUR take; the user should edit it so it's genuinely theirs."""
    title = (meta.get("title") or meta.get("hook_text") or "this moment").rstrip(".!?")
    cfg = settings()["llm"]
    if cfg.get("engine", "none") != "none":
        try:
            from .llm import complete
            prompt = ("Write ONE spoken sentence (max 22 words) that a creator says BEFORE showing this clip: a clear "
                      f"opinion or context that adds value. Clip: '{title}'. Transcript: "
                      f"'{(meta.get('highlight') or {}).get('hook_line', '')}'. Return JSON {{\"take\": \"...\"}}")
            m = re.search(r"\{.*\}", complete(prompt, cfg), re.S)
            if m:
                return json.loads(m.group(0))["take"][:240]
        except Exception as exc:
            log.warning("suggest_take LLM failed: %s", exc)
    return f"Here's why this matters: {title}. Watch what happens next."


def make_original(output_id: str, take_text: str, out_dir: Path, voice_file: Path | None = None,
                  engine: str | None = None, record: bool = True) -> dict:
    """Create `<output_id>_take`: a spoken intro card with your take, then the clip."""
    from .clipper import finalize
    out_dir = Path(out_dir).resolve()
    take_text = (take_text or "").strip()
    if not (8 <= len(take_text) <= 300) and not voice_file:
        raise TransformError("Write your take (8-300 characters) or upload your own voice clip")
    meta = json.loads((out_dir / f"{output_id}.json").read_text(encoding="utf-8"))
    clip = Path(meta["file"]).resolve()
    cfg = settings()["render"]
    w, h = ratio_dims(meta.get("ratio", "9:16"))
    fps = cfg["fps"]
    work = out_dir / f"_{output_id}_take"
    work.mkdir(parents=True, exist_ok=True)
    voice = Path(voice_file) if voice_file else speak(take_text, work / "take.wav", "en", engine)
    vdur = probe_duration(voice)
    if vdur > 12:
        raise TransformError("Keep your take under ~12 seconds so the clip still starts fast")
    idur = round(vdur + 0.45, 3)
    words = words_from_cues([(0.1, max(0.4, vdur - 0.05), take_text)]) if take_text else []
    ass = work / "take.ass"
    build_ass(words, ass, w, h, cfg["font"], None, "MY TAKE", min(idur, 2.0), "", idur, None,
              style=meta.get("caption_style", "bold_pop"))
    intro = work / "intro.mp4"
    # background = a frame of the SOURCE (no burned captions) at the clip start, blurred and darkened
    src = Path(meta.get("source_file", ""))
    bg_in, bg_ss = (src, float(meta.get("range", [0])[0]) + 0.5) if src.is_file() else (clip, 0.5)
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{bg_ss:.2f}", "-i", str(bg_in), "-i", str(voice),
         "-filter_complex",
         f"[0:v]trim=end_frame=1,loop=loop=-1:size=1,trim=0:{idur},setpts=PTS-STARTPTS,"
         f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1,"
         f"boxblur=24:2,eq=brightness=-0.28:saturation=0.6,fps={fps},format=yuv420p,ass={ass.name}[v];"
         f"[1:a]aresample=48000,apad,atrim=0:{idur},{loudnorm(cfg['loudness_lufs'])}[a]",
         "-map", "[v]", "-map", "[a]", "-t", str(idur), *encode_args(cfg["crf"], "veryfast", fps), intro.name], cwd=work)
    new_id = f"{output_id}_take"
    out = out_dir / f"{new_id}.mp4"
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(intro), "-i", str(clip), "-filter_complex",
         f"[0:v]setsar=1[v0];[1:v]setsar=1,fps={fps}[v1];[0:a]aresample=48000[a0];[1:a]aresample=48000[a1];"
         "[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]", "-map", "[v]", "-map", "[a]",
         *encode_args(cfg["crf"], cfg["preset"], fps), str(out)])
    # captions file: take words + original captions shifted
    src_srt = out_dir / f"{output_id}.srt"
    cues = [(s, e, t) for s, e, t in words]
    if src_srt.is_file():
        cues += [(s + idur, e + idur, t) for s, e, t in parse_srt(src_srt.read_text(encoding="utf-8"))]
    from .transcripts import write_srt
    write_srt(cues, out_dir / f"{new_id}.srt")
    new = dict(meta)
    new.update(output_id=new_id, file=str(out), duration_seconds=round(probe_duration(out), 2), added_commentary=True,
               take_text=take_text, voice="own" if voice_file else "tts", title=meta.get("title", ""),
               contains_realistic_synthetic_media=bool(meta.get("contains_realistic_synthetic_media"))
               or (not voice_file and _voicestudio_on()))
    new.pop("poster", None)
    return finalize(new, out_dir, None, record)


def zip_outputs(files: list[tuple[str, Path]], dest: Path) -> Path:
    import zipfile
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_STORED) as z:
        for name, path in files:
            z.write(path, arcname=name)
    return dest
