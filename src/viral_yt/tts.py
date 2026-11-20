"""Free, local voiceover. Order for engine=auto: VoiceStudio (only if VOICESTUDIO_URL is set) -> piper (natural, MIT)
-> ffmpeg flite (robotic fallback).

Your own recorded voice (a WAV you supply) always beats TTS for originality and retention.
Synthetic voices are fine on YouTube but are disclosed in our metadata when realistic (containsSyntheticMedia).
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .common import ROOT, settings


class TTSError(RuntimeError):
    pass


def _piper(text: str, out: Path, model: Path) -> Path:
    last = ""
    if not model.is_file():
        raise TTSError(f"piper voice model missing: {model} (run: py scripts/install.py --voice)")
    try:  # Python API (piper-tts >= 1.3 uses synthesize_wav; older releases use synthesize)
        import wave
        from piper import PiperVoice
        voice = PiperVoice.load(str(model))
        with wave.open(str(out), "wb") as wf:
            (getattr(voice, "synthesize_wav", None) or voice.synthesize)(text, wf)
        if out.is_file() and out.stat().st_size > 1000:
            return out
    except ImportError:
        pass
    except Exception as exc:  # fall through to CLI
        last = str(exc)
    exe = shutil.which("piper")
    if not exe:
        raise TTSError("piper not installed (pip install piper-tts)")
    for args in (["--model", str(model), "--output_file", str(out)], ["-m", str(model), "-f", str(out)]):
        proc = subprocess.run([exe, *args], input=text, text=True, capture_output=True)
        if proc.returncode == 0 and out.is_file():
            return out
        last = proc.stderr[-400:]
    raise TTSError(f"piper failed: {last}")


def _flite(text: str, out: Path, voice: str) -> Path:
    txt = out.with_suffix(".txt")
    txt.write_text(text, encoding="utf-8")
    # relative names + cwd avoid Windows drive-colon escaping inside the filtergraph
    proc = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                           "-i", f"flite=textfile={txt.name}:voice={voice}", "-ar", "44100", "-ac", "1", out.name],
                          capture_output=True, text=True, cwd=str(out.parent))
    if proc.returncode or not out.is_file():
        raise TTSError("No TTS engine: install piper-tts (recommended) or use an FFmpeg build with libflite. " + proc.stderr[-300:])
    return out


def synthesize(text: str, out: Path, engine: str | None = None) -> tuple[Path, str]:
    cfg = settings()["voice"]
    engine = engine or cfg["engine"]
    out.parent.mkdir(parents=True, exist_ok=True)
    errors = []
    from . import voicestudio
    order = (["voicestudio"] if voicestudio.enabled() else []) + ["piper", "flite"] if engine == "auto" else [engine]
    for name in order:
        try:
            if name == "voicestudio":
                try:
                    return voicestudio.speak(text, out, lang="en"), "voicestudio"
                except voicestudio.VoiceStudioError as exc:
                    raise TTSError(str(exc)) from exc
            if name == "piper":
                return _piper(text, out, ROOT / cfg["piper_model"]), "piper"
            if name == "flite":
                return _flite(text, out, cfg["flite_voice"]), "flite"
        except TTSError as exc:
            errors.append(str(exc))
    raise TTSError(" | ".join(errors) or f"Unknown TTS engine {engine}")
