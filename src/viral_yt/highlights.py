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
                    "i": i, "j": j, "score": round(sc, 2), "hook_line": cues[i][2], "text": " ".join(c[2] for c in cues[i:j + 1]),
                    "editorial_review_required": True})
    return sorted(out, key=lambda c: c["start"])


# ---------------------------------------------------------------------------
# Hybrid selection: local heuristic proposes, optional LLM judges only the shortlist.
# ---------------------------------------------------------------------------

def _sentence_hook(text: str) -> float:
    t = text.lower()
    return (sum(1.0 for w in HOOK_WORDS if w in t) + t.count("?") * 1.2 + t.count("!") * 0.6
            + len(re.findall(r"\b\d+(?:[.,]\d+)?%?\b", t)) * 0.8)


def pick_quote(sents, s0: int, s1: int) -> int | None:
    """Most quotable sentence (1.5-5 s long, not the opener) for an optional cold open."""
    best, best_score = None, 0.9
    for i in range(s0 + 1, s1 + 1):
        dur = sents[i][1] - sents[i][0]
        if 1.5 <= dur <= 5.0:
            sc = _sentence_hook(sents[i][2]) + (0.5 if dur <= 3.5 else 0)
            if sc > best_score:
                best, best_score = i, sc
    return best


def _windows(sents, min_s, max_s, energy):
    cues = [(s[0], s[1], s[2]) for s in sents]
    out = []
    for i in range(len(cues)):
        for j in range(i, len(cues)):
            dur = cues[j][1] - cues[i][0]
            if dur > max_s:
                break
            if dur >= min_s:
                out.append({"s0": i, "s1": j, "start": cues[i][0], "end": cues[j][1],
                            "heur": score_window(cues, i, j, energy)})
    return out


def _overlap(a, b) -> float:
    inter = max(0.0, min(a["end"], b["end"]) - max(a["start"], b["start"]))
    return inter / max(1e-6, min(a["end"] - a["start"], b["end"] - b["start"]))


def select_clips(sents, count: int = 3, min_s: float = 20, max_s: float = 58, energy=None,
                 use_llm: bool = False, completer=None) -> tuple[list[dict], dict]:
    """Return (clips, stats). sents = media_prep.sentences(words)."""
    stats = {"sentences": len(sents), "mode": "heuristic", "llm_tokens_est": 0}
    if not sents:
        return [], stats
    if use_llm:
        from .llm import LLMError, estimate_tokens, rerank
        wins = sorted(_windows(sents, min_s, max_s, energy), key=lambda w: w["heur"], reverse=True)
        shortlist: list[dict] = []
        k = min(16, max(8, count * 4))
        for w in wins:
            if all(_overlap(w, s) < 0.35 for s in shortlist):
                shortlist.append({**w, "id": len(shortlist)})
            if len(shortlist) >= k:
                break
        if shortlist:
            try:
                stats["llm_tokens_est"] = estimate_tokens(" ".join(
                    sents[i][2] for c in shortlist for i in range(c["s0"], c["s1"] + 1))) + 450
                judged = rerank(shortlist, sents, completer=completer)
                hs = sorted(c["heur"] for c in shortlist)
                rank = {c["id"]: 100 * hs.index(c["heur"]) / max(1, len(hs) - 1) for c in shortlist}
                picks = []
                for j in judged:
                    if j["score"] < 50:
                        continue
                    base = shortlist[j["id"]]
                    s0, s1 = j["s0"], j["s1"]
                    if sents[s1][1] - sents[s0][0] < min_s:      # tightened too far -> keep candidate span
                        s0, s1 = base["s0"], base["s1"]
                    picks.append({"s0": s0, "s1": s1, "start": sents[s0][0], "end": sents[s1][1],
                                  "score": round(0.7 * j["score"] + 0.3 * rank[j["id"]], 1), "title": j["title"],
                                  "hook_line": sents[s0][2], "quote": j["quote"] if j["quote"] is not None else pick_quote(sents, s0, s1),
                                  "reason": j["reason"], "source": "llm"})
                chosen: list[dict] = []
                for p in sorted(picks, key=lambda p: p["score"], reverse=True):
                    if all(_overlap(p, c) == 0 for c in chosen):
                        chosen.append(p)
                    if len(chosen) >= count:
                        break
                if chosen:
                    stats["mode"] = "hybrid-llm"
                    return sorted(chosen, key=lambda c: c["start"]), stats
            except LLMError as exc:
                stats["llm_error"] = str(exc)
    cues = [(s[0], s[1], s[2]) for s in sents]
    clips = find_highlights(cues, min_s, max_s, count, energy)
    out = []
    for c in clips:
        s0, s1 = c["i"], c["j"]
        out.append({**c, "s0": s0, "s1": s1, "title": "", "quote": pick_quote(sents, s0, s1), "reason": "heuristic",
                    "source": "heuristic"})
    return out, stats


# ---------------------------------------------------------------------------
# Clip score shown in the UI: 0-99 overall from three transparent parts. It's a transcript-signal
# estimate to help you pick and order clips, not a prediction of views.
# ---------------------------------------------------------------------------
PAYOFF_WORDS = ("so", "that's why", "the lesson", "turns out", "which means", "the answer", "result", "finally",
                "here's", "secret", "because", "remember")


def clip_scores(text: str, hook_line: str, duration: float) -> dict:
    t, h = (text or "").lower(), (hook_line or "").lower()
    words = len(t.split())
    hook = min(99, 35 + _sentence_hook(h) * 14 + (10 if len(h.split()) <= 12 else 0)
               - (25 if re.match(r"^(so|and|um|uh|yeah|like|but)\b", h) else 0))
    wps = words / max(duration, 1)
    flow = 99 - min(60, abs(wps - 2.7) * 22) - (0 if END_PUNCT.search(t.strip()) else 20) - min(20, abs(duration - 38) * .5)
    value = min(99, 30 + len(re.findall(r"\b\d+(?:[.,]\d+)?%?\b", t)) * 8 + t.count("?") * 6
                + sum(9 for w in PAYOFF_WORDS if w in t))
    parts = {k: int(max(5, min(99, round(v)))) for k, v in (("hook", hook), ("flow", flow), ("value", value))}
    parts["overall"] = int(round(parts["hook"] * .45 + parts["flow"] * .25 + parts["value"] * .30))
    return parts
