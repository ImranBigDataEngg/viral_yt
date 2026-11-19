"""Optional LLM re-ranking of highlight candidates (hybrid = fewer tokens than whole-transcript scanning).

Providers (all free-capable): Ollama (local), any OpenAI-compatible endpoint — Gemini's OpenAI-compatible API
(free tier), Groq, OpenRouter, DeepSeek, LM Studio. Configure in config/settings.json:
  "llm": {"engine": "openai", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
          "model": "gemini-2.5-flash", "api_key_env": "GEMINI_API_KEY"}
Only candidate TEXT is sent (never media). The model cites sentence indices it can see, never seconds.
"""
from __future__ import annotations

import json
import re
import urllib.request

from .common import env, get_logger, settings

log = get_logger("llm")

PROMPT = """You are a senior short-form video editor. Below are candidate passages from one long video, already cut
on sentence boundaries. Each sentence is tagged [s<index>]. Judge each candidate as a standalone YouTube Short /
TikTok / Reel for a viewer with ZERO context.

Criteria, in order: 1) HOOK — first line creates a question, tension, a contrarian claim or a specific number;
never opens with "so/yeah/and/um" or an answer to an unseen question. 2) SELF-CONTAINED — no unexplained
"he/that/like I said". 3) PAYOFF — something lands: a reveal, a rule, a laugh, a number. 4) QUOTABLE line.
You may tighten a candidate by choosing a later start or earlier end sentence INSIDE it. Never pad.
Score 0-100 and use the full range; below 50 means drop.

Return ONLY JSON: {"clips":[{"id":<candidate id>,"start":<s index>,"end":<s index>,"score":<int>,
"title":"<specific, <60 chars, not clickbait>","hook":"<opening words verbatim>",
"quote":<s index of the single most quotable sentence>,"reason":"<one sentence>"}]}

CANDIDATES:
"""


class LLMError(RuntimeError):
    pass


def _post(url: str, body: dict, headers: dict, timeout: int = 180) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def complete(prompt: str, cfg: dict | None = None) -> str:
    cfg = cfg or settings()["llm"]
    engine = cfg.get("engine", "none")
    if engine == "ollama":
        data = _post(cfg["ollama_url"].rstrip("/") + "/api/generate",
                     {"model": cfg["ollama_model"], "prompt": prompt, "stream": False, "format": "json",
                      "options": {"temperature": 0.2}}, {})
        return data["response"]
    if engine == "openai":
        key = env(cfg.get("api_key_env", "LLM_API_KEY"))
        data = _post(cfg["base_url"].rstrip("/") + "/chat/completions",
                     {"model": cfg["model"], "temperature": 0.2, "response_format": {"type": "json_object"},
                      "messages": [{"role": "user", "content": prompt}]}, {"Authorization": f"Bearer {key}"})
        return data["choices"][0]["message"]["content"]
    raise LLMError(f"LLM engine '{engine}' not configured")


def _parse(text: str, candidates: list[dict], n_sent: int) -> list[dict]:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("no JSON object in response")
    clips = json.loads(m.group(0)).get("clips")
    if not isinstance(clips, list):
        raise ValueError('"clips" must be a list')
    ids = {c["id"]: c for c in candidates}
    out = []
    for c in clips:
        cid = int(c["id"])
        if cid not in ids:
            raise ValueError(f"unknown candidate id {cid}")
        lo, hi = ids[cid]["s0"], ids[cid]["s1"]
        s, e = int(c.get("start", lo)), int(c.get("end", hi))
        s, e = max(lo, min(s, hi)), max(lo, min(e, hi))
        if e < s:
            s, e = lo, hi
        q = c.get("quote")
        out.append({"id": cid, "s0": s, "s1": e, "score": max(0, min(100, int(c.get("score", 0)))),
                    "title": str(c.get("title", ""))[:80], "hook": str(c.get("hook", ""))[:120],
                    "quote": int(q) if isinstance(q, (int, float)) and s <= int(q) <= e else None,
                    "reason": str(c.get("reason", ""))[:240]})
    return out


def rerank(candidates: list[dict], sents: list[tuple], cfg: dict | None = None, completer=None) -> list[dict]:
    """candidates: [{"id", "s0", "s1"}] over sentence indices. Returns judged clips (may be fewer)."""
    body = []
    for c in candidates:
        lines = " ".join(f"[s{i}] {sents[i][2]}" for i in range(c["s0"], c["s1"] + 1))
        body.append(f"<candidate id={c['id']}>\n{lines}\n</candidate>")
    prompt = PROMPT + "\n".join(body)
    call = completer or (lambda p: complete(p, cfg))
    last_err = ""
    for attempt in range(2):  # retry once with the validation error appended
        text = call(prompt if attempt == 0 else prompt + f"\n\nYour previous reply was invalid: {last_err}. Return valid JSON only.")
        try:
            return _parse(text, candidates, len(sents))
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            last_err = str(exc)
            log.warning("LLM rerank parse failed (attempt %d): %s", attempt + 1, exc)
    raise LLMError(f"LLM returned invalid JSON twice: {last_err}")


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)
