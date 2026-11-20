"""Action-moment detector for gameplay / streams / sports - no transcription needed (seconds, not minutes).

Signals, per second of the source:
  loud    momentary loudness (ebur128, reused from media_prep.audio_energy)
  sudden  how much louder this second is than the 3 s before it (gunshots, crashes, screams)
  motion  mean luma change between frames at 5 fps / 160 px wide (fast camera, explosions, chases)
  cuts    hard scene changes (spikes in that luma change)
Each signal is normalised per video, mixed with user weights (the dashboard sliders), smoothed, and the
highest peaks become clips. The window is placed so ~65% of it is build-up before the peak and 35% payoff,
then snapped to a nearby scene cut so the clip starts cleanly.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from .highlights import audio_energy
from .media_prep import work_dir

DEFAULT_WEIGHTS = {"loud": 1.0, "sudden": 1.0, "motion": 1.0, "cuts": 0.5}
LENGTHS = (30, 45, 60)


def motion_profile(source: Path) -> tuple[dict[int, float], list[float]]:
    """(mean luma change per second, scene-cut times). Cached per source."""
    cache = work_dir(source) / "motion.json"
    if cache.is_file():
        d = json.loads(cache.read_text())
        return {int(k): v for k, v in d["motion"].items()}, d["cuts"]
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-threads", "0", "-an", "-sn", "-i", str(source), "-vf",
         "fps=5,scale=160:-2,signalstats,metadata=mode=print:key=lavfi.signalstats.YDIF:file=-",
         "-f", "null", "-"], capture_output=True, text=True, timeout=1800)
    t, frames = None, []
    for line in proc.stdout.splitlines():
        if m := re.search(r"pts_time:([\d.]+)", line):
            t = float(m.group(1))
        elif (m := re.search(r"YDIF=([\d.]+)", line)) and t is not None:
            frames.append((t, float(m.group(1))))
    per: dict[int, list[float]] = {}
    for tt, v in frames[1:]:                      # first frame has no predecessor
        per.setdefault(int(tt), []).append(v)
    motion = {s: sum(v) / len(v) for s, v in per.items()}
    vals = sorted(v for _, v in frames[1:]) or [0.0]
    med = vals[len(vals) // 2]
    cut_th = max(25.0, med * 5)
    cuts, last = [], -10.0
    for tt, v in frames[1:]:
        if v >= cut_th and tt - last > 1.0:
            cuts.append(round(tt, 2))
            last = tt
    cache.write_text(json.dumps({"motion": motion, "cuts": cuts}))
    return motion, cuts


def _norm(series: list[float]) -> list[float]:
    """Robust 0..1 scaling: median -> 0, 95th percentile -> 1 (outliers capped)."""
    if not series:
        return []
    s = sorted(series)
    lo, hi = s[len(s) // 2], s[min(len(s) - 1, int(len(s) * 0.95))]
    span = (hi - lo) or 1e-6
    return [min(1.25, max(0.0, (x - lo) / span)) for x in series]


def second_scores(energy: dict[int, float], motion: dict[int, float], cuts: list[float], secs: int,
                  weights: dict | None = None, with_parts: bool = False):
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    floor = -70.0
    loud = [max(floor, energy.get(t, floor)) for t in range(secs)]
    sudden = [max(0.0, loud[t] - sum(loud[max(0, t - 3):t]) / max(1, len(loud[max(0, t - 3):t]))) if t else 0.0
              for t in range(secs)]
    mot = [motion.get(t, 0.0) for t in range(secs)]
    cut = [0.0] * secs
    for c in cuts:                                 # cut density: a burst of cuts = montage / action
        for t in range(max(0, int(c) - 2), min(secs, int(c) + 3)):
            cut[t] += 1.0
    comps = {"loud": _norm(loud), "sudden": _norm(sudden), "motion": _norm(mot), "cuts": _norm(cut)}
    total = sum(max(0.0, float(w[k])) for k in comps) or 1.0
    raw = [sum(max(0.0, float(w[k])) * comps[k][t] for k in comps) / total for t in range(secs)]
    smooth = [sum(raw[max(0, t - 1):t + 2]) / len(raw[max(0, t - 1):t + 2]) for t in range(secs)]   # 3 s smooth
    return (smooth, comps) if with_parts else smooth


def _window_level(series: list[float], a: float, b: float) -> int:
    win = sorted(series[int(a):int(b)] or [0.0], reverse=True)
    top = win[:max(1, len(win) // 3)]
    return int(max(5, min(99, round(20 + 79 * min(1.0, sum(top) / len(top))))))


def action_moments(source: Path, duration: float, count: int = 5, length: float | str = 30,
                   weights: dict | None = None, min_s: float = 15, max_s: float = 60) -> list[dict]:
    secs = int(duration)
    if secs < min_s:
        return []
    length = 30 if length in (None, "", "auto") else float(length)
    length = max(min_s, min(float(max_s), length, float(secs)))
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(2) as pool:              # audio + video passes run side by side
        fe, fm = pool.submit(audio_energy, source), pool.submit(motion_profile, source)
        energy, (motion, cuts) = fe.result(), fm.result()
    sc, comps = second_scores(energy, motion, cuts, secs, weights, with_parts=True)
    if max(sc, default=0) <= 0:
        return []
    order = sorted(range(secs), key=lambda t: sc[t], reverse=True)
    taken: list[tuple[float, float]] = []
    picks = []
    for peak in order:
        if len(picks) >= count:
            break
        a = max(0.0, min(secs - length, peak - length * 0.65))
        near = [c for c in cuts if a - 2.0 <= c <= a + 2.0 and c <= peak - 3]
        if near:
            a = min(near, key=lambda c: abs(c - a))
        b = min(float(secs), a + length)
        if any(a < tb and b > ta for ta, tb in taken):
            continue
        win = sc[int(a):int(b)] or [0.0]
        top = sorted(win, reverse=True)[:max(1, len(win) * 2 // 5)]
        picks.append({"start": round(a, 2), "end": round(b, 2), "peak": peak, "raw": sum(top) / len(top) + 0.3 * sc[peak]})
        taken.append((a, b))
    if not picks:
        return []
    hi = max(p["raw"] for p in picks)
    base = sorted(sc)[len(sc) // 2]
    best = max(picks, key=lambda p: p["raw"])
    picks = [p for p in picks if p is best or p["raw"] > base + 0.15 * (hi - base) + 0.05]   # drop dead air
    for p in picks:                                # 0-99 score, like the speech clips
        p["score"] = int(round(40 + 59 * max(0.0, min(1.0, (p["raw"] - base) / ((hi - base) or 1e-6)))))
    out = [{"start": p["start"], "end": p["end"], "duration": round(p["end"] - p["start"], 2), "score": p["score"],
            "virality": p["score"], "peak_at": p["peak"], "hook_line": "", "title": "", "quote": None,
            "source": "action", "reason": "action: loudness, sudden sounds, motion and scene cuts",
            "signals": {k: _window_level(comps[k], p["start"], p["end"]) for k in ("loud", "sudden", "motion")}}
           for p in picks]
    return sorted(out, key=lambda c: c["start"])
