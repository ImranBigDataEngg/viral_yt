"""AI social copy per clip: title, caption and hashtags for YouTube Shorts, TikTok and Instagram Reels.

LLM (if configured in settings.llm) writes it from the clip's own transcript; otherwise a keyword heuristic does.
Rules either way: specific, not clickbait, no fake claims, hashtags drawn from what's actually in the clip,
max 5 hashtags (platforms down-rank tag spam). Editable by the user before every post.
"""
from __future__ import annotations

import json
import re
from collections import Counter

from .common import get_logger, settings

log = get_logger("social")

STOP = set("""a about above after again all also am an and any are as at be because been before being below between both
but by can could did do does doing down during each even ever every few for from further get got gonna had has have
having he her here hers him his how i if in into is it its just know like look make me more most much my need no nor
not now of off on once only or other our out over own really right said same say see she should so some still such
than that the their them then there these they thing things think this those through to too um uh under until up us
very want was way we well were what when where which while who why will with would yeah yes you your going okay
actually literally gonna wanna kind sort lot maybe something anything everything nothing guys""".split())

PROMPT = """Write social media copy for ONE short vertical video clip. Be specific to what is said/shown, no clickbait,
no claims that aren't in the clip, no emojis in the title. Max 5 hashtags, lowercase, relevant, no #fyp/#viral spam.
Return ONLY JSON:
{{"title":"<YouTube Shorts title, <70 chars>","caption":"<1-2 sentence hook caption for TikTok/Instagram, <180 chars>",
"hashtags":["#tag1","#tag2"]}}

Video source title: {source_title}
Category: {category}
Clip hook: {hook}
Clip transcript: {transcript}
"""


def _keywords(text: str, n: int = 4) -> list[str]:
    text = text or ""
    combos = [f"{a}{b}".lower() for a, b in re.findall(r"\b([A-Za-z]{2,5})\s?(\d{1,2})\b", text)]   # GTA 6 -> gta6
    words = [w.lower() for w in re.findall(r"[A-Za-z][A-Za-z0-9']{2,}", text)]
    cnt = Counter(w.strip("'") for w in words if w not in STOP and len(w) >= 4)
    out = list(dict.fromkeys(combos))
    for w, _ in cnt.most_common(n * 2):
        if not any(w[:5] == o[:5] for o in out):          # budgets ~ budgeting: keep one
            out.append(w)
    return out[:n]


def _tag(word: str) -> str:
    return "#" + re.sub(r"[^a-z0-9]", "", word.lower())


def heuristic(meta: dict) -> dict:
    src_title = (meta.get("source_title") or "").strip()
    transcript = (meta.get("transcript") or "").strip()
    hook = (meta.get("hook_text") or "").strip()
    title = (meta.get("title") or "").strip()
    if not title or title == hook and not transcript:       # gameplay clip: name the game/moment, tease the payoff
        title = f"{src_title} — {hook.rstrip('…!.').lower()}" if src_title and hook else (src_title or hook or "Highlight")
    kws = _keywords(src_title + " " + src_title + " " + transcript)
    cat = (meta.get("category") or "").strip().lower()
    tags = []
    for t in ([cat] if cat else []) + kws + ([] if transcript else ["gaming"] if meta.get("detect") == "action" else []):
        tg = _tag(t)
        if len(tg) > 2 and tg not in tags:
            tags.append(tg)
    tags = tags[:5]
    first = re.split(r"(?<=[.!?])\s+", transcript)[0] if transcript else ""
    caption = first if 15 <= len(first) <= 180 else (hook or title)
    if src_title and src_title.lower() not in caption.lower() and len(caption) + len(src_title) < 170:
        caption = f"{caption} ({src_title})" if caption != src_title else caption
    return {"title": title[:70], "caption": caption[:180], "hashtags": tags, "engine": "heuristic"}


def _clean(d: dict, fallback: dict) -> dict:
    tags = []
    for t in d.get("hashtags") or []:
        tg = _tag(str(t).lstrip("#"))
        if len(tg) > 2 and tg not in tags and tg not in ("#fyp", "#viral", "#foryou", "#foryoupage"):
            tags.append(tg)
    return {"title": (str(d.get("title") or "").strip() or fallback["title"])[:70],
            "caption": (str(d.get("caption") or "").strip() or fallback["caption"])[:180],
            "hashtags": tags[:5] or fallback["hashtags"], "engine": "llm"}


def generate(meta: dict) -> dict:
    """Return {"title","caption","hashtags","engine","platforms":{...}} for this clip."""
    base = heuristic(meta)
    cfg = settings().get("llm", {})
    if cfg.get("engine", "none") != "none" and cfg.get("social_copy", True):
        try:
            from .llm import complete
            text = complete(PROMPT.format(source_title=meta.get("source_title") or "-", category=meta.get("category") or "-",
                                          hook=meta.get("hook_text") or "-", transcript=(meta.get("transcript") or "-")[:1800]))
            m = re.search(r"\{.*\}", text, re.S)
            if m:
                base = _clean(json.loads(m.group(0)), base)
        except Exception as exc:              # copy is a convenience: never fail a render over it
            log.warning("LLM social copy failed, using heuristic: %s", exc)
    return {**base, "platforms": platform_copy(base)}


def platform_copy(c: dict) -> dict:
    tags = " ".join(c["hashtags"])
    return {
        "youtube": {"title": c["title"], "description": f"{c['caption']}\n\n{tags} #shorts".strip()},
        "tiktok": {"caption": f"{c['caption']} {tags}".strip()[:2200]},
        "instagram": {"caption": f"{c['caption']}\n\n{tags}".strip()[:2200]},
    }
