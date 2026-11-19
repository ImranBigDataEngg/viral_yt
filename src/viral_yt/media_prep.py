"""Per-source preparation with a content-addressed cache (output/work/<fingerprint>/):
16 kHz mono audio, silences, word-level transcript, sentence cues. Re-runs reuse every stage."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path

from .common import ROOT, local_file
from .transcripts import parse_srt, words_from_cues

SENT_END = re.compile(r"[.!?…]['\")\]]?$")


def fingerprint(path: Path) -> str:
    """Fast stable id: size + first/last 1 MiB (no full-file hash of multi-GB sources)."""
    h = hashlib.sha1()
    size = path.stat().st_size
    h.update(str(size).encode())
    with path.open("rb") as f:
        h.update(f.read(1 << 20))
        if size > 2 << 20:
            f.seek(-(1 << 20), 2)
            h.update(f.read(1 << 20))
    return h.hexdigest()[:16]


def work_dir(source: Path) -> Path:
    d = ROOT / "output" / "work" / fingerprint(source)
    d.mkdir(parents=True, exist_ok=True)
    return d


def extract_audio(source: Path) -> Path:
    out = work_dir(source) / "audio16k.wav"
    if not out.is_file():
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source), "-vn", "-ac", "1",
                        "-ar", "16000", str(out)], check=True)
    return out


def silences(source: Path, noise_db: float = -32.0, min_s: float = 0.2) -> list[tuple[float, float]]:
    cache = work_dir(source) / "silences.json"
    if cache.is_file():
        return [tuple(x) for x in json.loads(cache.read_text())]
    proc = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(extract_audio(source)), "-af",
                           f"silencedetect=noise={noise_db}dB:d={min_s}", "-f", "null", "-"],
                          capture_output=True, text=True)
    out, start = [], None
    for line in proc.stderr.splitlines():
        if m := re.search(r"silence_start:\s*([-\d.]+)", line):
            start = float(m.group(1))
        elif (m := re.search(r"silence_end:\s*([-\d.]+)", line)) and start is not None:
            out.append((max(0.0, start), float(m.group(1))))
            start = None
    cache.write_text(json.dumps(out))
    return out


def load_words(asset: dict, source: Path, allow_asr: bool = True) -> list[tuple[float, float, str]]:
    """Best available word timings: words.json next to the SRT > cached ASR > SRT spread > fresh ASR."""
    tf = (asset.get("transcript_file") or "").strip()
    if tf:
        srt = local_file(tf)
        wj = srt.with_suffix(".words.json")
        if wj.is_file():
            return [tuple(w) for w in json.loads(wj.read_text(encoding="utf-8"))]
        if srt.is_file():
            return words_from_cues(parse_srt(srt.read_text(encoding="utf-8-sig")))
    cached = work_dir(source) / "words.json"
    if cached.is_file():
        return [tuple(w) for w in json.loads(cached.read_text())]
    if not allow_asr:
        return []
    from .transcripts import transcribe_owned_file
    srt = transcribe_owned_file(extract_audio(source), work_dir(source) / "transcript.srt")
    words = json.loads(srt.with_suffix(".words.json").read_text(encoding="utf-8"))
    cached.write_text(json.dumps(words))
    return [tuple(w) for w in words]


def sentences(words) -> list[tuple[float, float, str, int, int]]:
    """Group words into sentences: (start, end, text, first_word_idx, last_word_idx)."""
    out, first = [], 0
    for i, (_, _, w) in enumerate(words):
        nxt_gap = words[i + 1][0] - words[i][1] if i + 1 < len(words) else 99
        if SENT_END.search(w.strip()) or nxt_gap > 1.2 or i == len(words) - 1 or i - first >= 45:
            seg = words[first:i + 1]
            out.append((seg[0][0], seg[-1][1], " ".join(x[2] for x in seg), first, i))
            first = i + 1
    return out


def align_cut(t: float, sil: list[tuple[float, float]], side: str, radius: float = 0.75) -> float:
    """Move a cut into the nearest measured silence (where an editor would cut) instead of a fixed pad."""
    best, dist = None, radius
    for a, b in sil:
        if side == "start" and a - radius <= t <= b + radius:
            cand = max(a + 0.04, min(t - 0.12, b - 0.04))      # just inside the trough, before the breath
        elif side == "end" and a - radius <= t <= b + radius:
            cand = min(b - 0.04, max(t + 0.28, a + 0.04))      # hold a beat after the last word
        else:
            continue
        if abs(cand - t) <= dist:
            best, dist = cand, abs(cand - t)
    if best is None:
        return max(0.0, t - 0.25) if side == "start" else t + 0.35
    return max(0.0, best)
