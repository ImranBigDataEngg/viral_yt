"""SRT helpers and optional offline transcription (faster-whisper) of AUTHORIZED local media only."""
from __future__ import annotations

import json
import re
from pathlib import Path

Cue = tuple[float, float, str]


def seconds_to_srt(value: float) -> str:
    msec = int(round(max(value, 0) * 1000))
    hours, rest = divmod(msec, 3600000)
    minutes, rest = divmod(rest, 60000)
    seconds, milliseconds = divmod(rest, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02},{milliseconds:03}"


def srt_to_seconds(value: str) -> float:
    match = re.fullmatch(r"(\d+):(\d{2}):(\d{2})[,.](\d{3})", value.strip())
    if not match:
        raise ValueError(f"Bad SRT timestamp: {value}")
    h, m, s, ms = map(int, match.groups())
    return h * 3600 + m * 60 + s + ms / 1000


def parse_srt(content: str) -> list[Cue]:
    blocks = re.split(r"\n\s*\n", content.replace("\r\n", "\n").strip())
    results = []
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        idx = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if idx is None:
            continue
        left, right = lines[idx].split("-->", 1)
        start, end = srt_to_seconds(left), srt_to_seconds(right.strip().split()[0])
        words = " ".join(lines[idx + 1:])
        if start < end and words:
            results.append((start, end, words))
    return results


def write_srt(cues: list[Cue], target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as out:
        for i, (b, e, t) in enumerate(cues, 1):
            out.write(f"{i}\n{seconds_to_srt(b)} --> {seconds_to_srt(e)}\n{t}\n\n")
    return target


def slice_cues(cues: list[Cue], start: float, end: float) -> list[Cue]:
    out = []
    for b, e, t in cues:
        cb, ce = max(b, start), min(e, end)
        if cb < ce:
            out.append((cb - start, ce - start, t))
    return out


def words_from_cues(cues: list[Cue]) -> list[tuple[float, float, str]]:
    """Approximate word timings by spreading each cue's duration over its words (weighted by length)."""
    words = []
    for b, e, text in cues:
        tokens = text.split()
        if not tokens:
            continue
        total = sum(len(t) + 1 for t in tokens)
        cursor = b
        for tok in tokens:
            span = (e - b) * (len(tok) + 1) / total
            words.append((cursor, cursor + span, tok))
            cursor += span
    return words


def select_highlight(cues: list[Cue], max_seconds: int = 50) -> tuple[float, float, str]:
    """Back-compat single best window; see highlights.find_highlights for multi-clip selection."""
    from .highlights import find_highlights
    found = find_highlights(cues, min_seconds=6, max_seconds=max_seconds, count=1)
    if not found:
        raise ValueError("Captions did not include a 6-second passage")
    h = found[0]
    return h["start"], h["end"], h["text"]


def _cuda_dll_dirs() -> None:
    """Windows: make pip-installed CUDA libraries (nvidia-cublas-cu12, nvidia-cudnn-cu12) visible to CTranslate2."""
    import os
    import sys
    if os.name != "nt":
        return
    for base in map(Path, sys.path):
        nv = base / "nvidia"
        if nv.is_dir():
            for b in nv.glob("*/bin"):
                try:
                    os.add_dll_directory(str(b))
                except OSError:
                    pass
                os.environ["PATH"] = str(b) + os.pathsep + os.environ.get("PATH", "")


def asr_device() -> tuple[str, str]:
    """(device, compute_type). settings.asr.device / TT_ASR_DEVICE: auto | cuda | cpu.
    auto = NVIDIA GPU if CTranslate2 sees one (GTX 1650 etc: int8_float16, ~4-8x faster than CPU), else CPU int8."""
    from .common import env, settings
    want = (env("TT_ASR_DEVICE") or settings().get("asr", {}).get("device", "auto")).lower()
    if want != "cpu":
        try:
            _cuda_dll_dirs()
            import ctranslate2
            if ctranslate2.get_cuda_device_count() > 0:
                return "cuda", settings().get("asr", {}).get("cuda_compute_type", "int8_float16")
        except Exception:
            pass
    return "cpu", "int8"


def _run_whisper(model_size: str, device: str, ctype: str, media: Path):
    from faster_whisper import WhisperModel
    model = WhisperModel(model_size, device=device, compute_type=ctype)
    segments, _ = model.transcribe(str(media), beam_size=5, word_timestamps=True, vad_filter=True)
    cues, words = [], []
    for seg in segments:                      # segments is lazy: GPU library errors surface here
        cues.append((seg.start, seg.end, seg.text.strip()))
        for w in seg.words or []:
            words.append([round(w.start, 3), round(w.end, 3), w.word.strip()])
    return cues, words


def transcribe_owned_file(media: Path, output_srt: Path, model_size: str | None = None, words_json: bool = True) -> Path:
    import os
    from .common import get_logger, settings
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")   # harmless on Windows without Developer Mode
    try:
        import faster_whisper  # noqa: F401
    except ImportError as exc:
        raise RuntimeError("Install optional requirements: pip install -r requirements-optional.txt") from exc
    model_size = model_size or settings().get("asr", {}).get("model", "small")
    device, ctype = asr_device()
    try:
        cues, words = _run_whisper(model_size, device, ctype, media)
    except (RuntimeError, OSError, ValueError) as exc:
        if device == "cpu":
            raise
        get_logger("asr").warning("GPU transcription failed (%s); falling back to CPU. Fix: "
                                  "pip install nvidia-cublas-cu12 nvidia-cudnn-cu12==9.* (see docs/MOBILE.md#speed)", exc)
        cues, words = _run_whisper(model_size, "cpu", "int8", media)
    write_srt(cues, output_srt)
    if words_json:
        output_srt.with_suffix(".words.json").write_text(json.dumps(words), encoding="utf-8")
    return output_srt
