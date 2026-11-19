"""Find several non-overlapping, self-contained 20–58 s highlight windows in a transcript.

Signals (all local, no AI service required):
  * hook language in the first sentence (questions, numbers, contrast words, superlatives)
  * complete idea arc: window starts at a sentence start and ends at a sentence end
  * pacing: words per second in a lively-but-clear band
  * optional audio energy per second (from ffmpeg ebur128) — laughter/excitement peaks
Output is a SUGGESTION list for editorial review, never auto-published as-is.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

HOOK_WORDS = ("why", "how", "secret", "nobody", "never", "actually", "biggest", "first", "insane", "crazy",
              "truth", "mistake", "wrong", "finally", "confirmed", "leak", "real reason", "here's", "wait",
              "but", "imagine", "what if", "the problem")
END_PUNCT = re.compile(r"[.!?…]['\")\]]?$")


def _sentence_starts(cues) -> set[int]:
    starts = {0}
    for i in range(1, len(cues)):
        prev = cues[i - 1][2].strip()
        gap = cues[i][0] - cues[i - 1][1]
        if END_PUNCT.search(prev) or gap > 0.8:
            starts.add(i)
    return starts


def audio_energy(media: Path) -> dict[int, float]:
    """Per-second momentary loudness (LUFS) via ffmpeg ebur128; empty dict on failure."""
    try:
        proc = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(media), "-af", "ebur128=framelog=info",
                               "-f", "null", "-"], capture_output=True, text=True, timeout=900)
    except (OSError, subprocess.TimeoutExpired):
        return {}
    energy: dict[int, float] = {}
    for m in re.finditer(r"t:\s*([\d.]+)\s+TARGET.*?M:\s*(-?[\d.]+)", proc.stderr):
        sec, val = int(float(m.group(1))), float(m.group(2))
        energy[sec] = max(energy.get(sec, -120.0), val)
    return energy


def score_window(cues, i: int, j: int, energy: dict[int, float] | None = None) -> float:
    start, end = cues[i][0], cues[j][1]
    text = " ".join(c[2] for c in cues[i:j + 1])
    lower = text.lower()
    first = cues[i][2].lower()
    words = len(text.split())
    duration = end - start
    s = 0.0
    s += sum(2.5 for w in HOOK_WORDS if w in first)               # strong opening line
    s += sum(0.6 for w in HOOK_WORDS if w in lower)
    s += lower.count("?") * 1.5 + len(re.findall(r"\b\d+(?:[.,]\d+)?%?\b", lower)) * 0.8
    s += 3.0 if END_PUNCT.search(cues[j][2].strip()) else -3.0    # ends on a complete thought
    wps = words / max(duration, 1)
    s -= abs(wps - 2.7) * 2.0                                      # ~2.7 words/s reads well on Shorts
    s -= abs(duration - 38) * 0.06                                 # mild preference for ~38 s
    if energy:
        vals = [energy[k] for k in range(int(start), int(end) + 1) if k in energy]
        if vals:
            s += (max(vals) - (sum(vals) / len(vals))) * 0.25      # reward peaks (laughs, reactions)
    return s


def find_highlights(cues, min_seconds: float = 20, max_seconds: float = 58, count: int = 3,
                    energy: dict[int, float] | None = None) -> list[dict]:
    """Choose up to `count` non-overlapping windows that start at a sentence start and end on a complete
    thought, maximising (number of clips, total score) with a small dynamic programme."""
    if not cues:
        return []
    starts = _sentence_starts(cues)
    by_start: dict[int, list[tuple[float, int]]] = {}
    for i in starts:
        for j in range(i, len(cues)):
            dur = cues[j][1] - cues[i][0]
            if dur > max_seconds:
                break
            if dur >= min_seconds and (END_PUNCT.search(cues[j][2].strip()) or j == len(cues) - 1):
                by_start.setdefault(i, []).append((score_window(cues, i, j, energy), j))
    n = len(cues)
    # best[k][i] = (clips, total_score, picks) using cues[i:] and at most k clips
    best = [[(0, 0.0, ())] * (n + 1) for _ in range(count + 1)]
    for k in range(1, count + 1):
        row, prev = best[k], best[k - 1]
        for i in range(n - 1, -1, -1):
            choice = row[i + 1]
            for sc, j in by_start.get(i, []):
                c, t, picks = prev[j + 1]
                cand = (c + 1, t + sc, ((i, j, sc),) + picks)
                if (cand[0], cand[1]) > (choice[0], choice[1]):
                    choice = cand
            row[i] = choice
    out = []
    for i, j, sc in best[count][0][2]:
        start, end = cues[i][0], cues[j][1]
        out.append({"start": round(start, 2), "end": round(end, 2), "duration": round(end - start, 2),
                    "score": round(sc, 2), "hook_line": cues[i][2], "text": " ".join(c[2] for c in cues[i:j + 1]),
                    "editorial_review_required": True})
    return sorted(out, key=lambda c: c["start"])
