"""Turn ranked trend candidates into ORIGINAL Short briefs (script + metadata) for human review.

The trending video is a research pointer only. Nothing from it is rendered. Drafts contain [VERIFY]
markers that block rendering until a human checks facts and removes them.
"""
from __future__ import annotations

import json
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from .common import ROOT, get_logger, safe_name, settings

log = get_logger("scriptgen")
BLOCKERS = re.compile(r"\[(VERIFY|TODO|FILL)[^\]]*\]", re.I)

HOOKS = {
    "Myth": ["Half of what you heard about {topic} is wrong.", "{topic}: here's what's actually confirmed.",
             "Stop believing this {topic} rumour."],
    "details": ["You missed this in {topic}.", "3 things nobody noticed about {topic}.",
                "Watch {topic} again — look at this."],
    "verdict": ["{topic}: which one actually wins?", "I compared them so you don't have to.",
                "The {topic} winner surprised me."],
    "default": ["Why is everyone talking about {topic}?", "{topic} blew up overnight. Here's why.",
                "{topic} in 45 seconds."],
}


def short_topic(title: str, term: str) -> str:
    t = re.sub(r"[\[(].*?[\])]|#\w+|\|.*$", "", title).strip(" -:|")
    return term if len(t) > 48 or not t else t


def template_brief(c: dict) -> dict:
    topic = short_topic(c.get("title", ""), c.get("search_term", "this"))
    angle = c.get("angle_hint", "")
    key = "Myth" if "Myth" in angle else "details" if "missed" in angle else "verdict" if "verdict" in angle else "default"
    hooks = [h.format(topic=topic) for h in HOOKS[key]]
    script = {
        "hook": hooks[0],
        "context": f"[VERIFY: one-sentence what happened with {topic}, from an official or primary source] ",
        "beats": ["[VERIFY: fact 1 + source]", "[VERIFY: fact 2 + source]", "[FILL: your own opinion or test result]"],
        "payoff": "[FILL: the takeaway — what it means for the viewer]",
        "cta": "Follow for the story behind every trend.",
    }
    return {"hook_options": hooks, "script": script}


def ollama_brief(c: dict, cfg: dict) -> dict | None:
    prompt = (
        "You write ORIGINAL 40-55 second YouTube Shorts scripts for the channel TrendTide. "
        "Topic signal (do not copy it, do not summarise the other creator's video): "
        f"title='{c.get('title')}', search term='{c.get('search_term')}', suggested angle='{c.get('angle_hint')}'. "
        "Rules: 110-140 words total; hook <= 10 words; never invent facts, numbers, dates or quotes — wrap every "
        "factual claim that needs checking in [VERIFY: ...]; no clickbait that the video doesn't pay off; "
        "include one line marked [FILL: creator opinion]. Return JSON only with keys: hook_options (3 strings), "
        "script {hook, context, beats (3 strings), payoff, cta}, title_options (3, <= 60 chars), tags (6)."
    )
    body = json.dumps({"model": cfg["ollama_model"], "prompt": prompt, "stream": False, "format": "json"}).encode()
    try:
        req = urllib.request.Request(cfg["ollama_url"].rstrip("/") + "/api/generate", data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=180) as r:
            data = json.loads(json.load(r)["response"])
        assert "script" in data and "hook_options" in data
        return data
    except Exception as exc:  # local LLM is optional
        log.warning("ollama unavailable, using template: %s", exc)
        return None


def make_brief(c: dict) -> dict:
    cfg = settings()
    draft = (ollama_brief(c, cfg["llm"]) if cfg["llm"]["engine"] == "ollama" else None) or template_brief(c)
    topic = short_topic(c.get("title", ""), c.get("search_term", ""))
    brand = cfg["brand"]
    tags = draft.get("tags") or [c.get("search_term", ""), c.get("category", ""), "trending", "explained", "news", "shorts"]
    titles = draft.get("title_options") or [f"{topic}: what's really going on", f"Why {topic} is everywhere",
                                             f"{topic} explained in 45s"]
    bid = safe_name(f"{c.get('category','x')}_{c.get('video_id','')}_{datetime.now(timezone.utc):%Y%m%d}")
    return {
        "brief_id": bid, "status": "draft", "kind": "explainer", "category": c.get("category"), "topic": topic,
        "angle": c.get("angle_hint"), "research_pointer": {"url": c.get("url"), "title": c.get("title"),
                                                            "channel": c.get("channel"), "views": c.get("views")},
        **draft, "title_options": titles, "title": titles[0][:100],
        "description": f"{topic} — the quick, original breakdown. {brand['tagline']}\n\nSources: [FILL: list sources you used]",
        "tags": [t for t in tags if t][:10], "hashtags": brand["hashtags"],
        "visuals": [], "visual_rights": "[FILL: original / licensed / public_domain + evidence, or leave visuals empty for generated motion background]",
        "voice_file": "", "synthetic_voice": True, "contains_realistic_synthetic_media": False,
        "approved": False, "publish_approved": False,
    }


def write_briefs(candidates: list[dict], per_category: int = 3) -> list[Path]:
    out = ROOT / "output" / "briefs"
    out.mkdir(parents=True, exist_ok=True)
    paths, counts = [], {}
    for c in sorted(candidates, key=lambda r: (r.get("category", ""), -float(r.get("score", 0)))):
        cat = c.get("category", "")
        if counts.get(cat, 0) >= per_category or c.get("is_short") in (True, "True"):
            continue
        brief = make_brief(c)
        path = out / f"{brief['brief_id']}.json"
        if not path.exists():  # never overwrite a brief a human may have edited
            path.write_text(json.dumps(brief, indent=2, ensure_ascii=False), encoding="utf-8")
        counts[cat] = counts.get(cat, 0) + 1
        paths.append(path)
    return paths


def script_text(brief: dict) -> str:
    s = brief["script"]
    parts = [s.get("hook", ""), s.get("context", ""), *s.get("beats", []), s.get("payoff", ""), s.get("cta", "")]
    return " ".join(p.strip() for p in parts if p and p.strip())


def check_ready(brief: dict) -> list[str]:
    problems = []
    blob = json.dumps({k: brief.get(k) for k in ("script", "title", "description", "visual_rights")})
    if BLOCKERS.search(blob):
        problems.append("Unresolved [VERIFY]/[FILL]/[TODO] markers remain in script/title/description/visual_rights")
    words = len(script_text(brief).split())
    if not 35 <= words <= 190:
        problems.append(f"Script length {words} words; aim for 90-150 (≈35-58 s)")
    if not brief.get("approved"):
        problems.append("Brief not approved (set \"approved\": true after review)")
    return problems


def load_brief(path_or_id: str) -> tuple[Path, dict]:
    p = Path(path_or_id)
    if not p.is_file():
        p = ROOT / "output" / "briefs" / f"{path_or_id}.json"
    if not p.is_file():
        raise FileNotFoundError(f"Brief not found: {path_or_id}")
    return p, json.loads(p.read_text(encoding="utf-8"))
