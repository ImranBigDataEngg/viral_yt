"""Monetization-readiness heuristic (0-100). Not YouTube's system — a guardrail against 'inauthentic'
(mass-produced / reused) uploads. Publishing requires score >= publishing.min_originality_score."""
from __future__ import annotations

from difflib import SequenceMatcher


def score(meta: dict, published_titles: list[str] | None = None) -> tuple[int, list[str]]:
    pts, why = 0, []
    kind = meta.get("kind")
    basis = meta.get("rights_basis", "")
    voice = meta.get("voice")  # "own" | "tts" | None
    if voice == "own":
        pts += 35; why.append("+35 own recorded voice-over")
    elif voice == "tts":
        pts += 22; why.append("+22 synthetic voice-over of an original script")
    elif basis == "original" and meta.get("source_audio") == "full" and kind == "clip":
        pts += 25; why.append("+25 original audio from your own recording")
    else:
        why.append("+0 no added narration")
    if kind == "explainer":
        pts += 25; why.append("+25 original script written for this Short")
        pts += 15 if meta.get("visuals_kind") in ("own", "generated") else 8
        why.append("+15 own/generated visuals" if meta.get("visuals_kind") in ("own", "generated") else "+8 licensed visuals")
    else:
        if basis == "original":
            pts += 30; why.append("+30 footage is your own")
        else:
            pts += 8; why.append("+8 third-party footage used under licence/permission")
        if meta.get("source_audio") == "full" and basis != "original":
            pts -= 15; why.append("-15 third-party audio kept at full level")
    if meta.get("captions_included"):
        pts += 10; why.append("+10 captions")
    if meta.get("hook_text"):
        pts += 5; why.append("+5 hook title card")
    if meta.get("layout") in ("blurfit", "crop"):
        pts += 5; why.append("+5 re-framed for 9:16")
    title = (meta.get("title") or "").lower()
    for old in published_titles or []:
        if old and SequenceMatcher(None, title, old.lower()).ratio() > 0.85:
            pts -= 25; why.append(f"-25 near-duplicate of published title '{old}'")
            break
    return max(0, min(100, pts)), why
