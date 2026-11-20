"""EzzyClips Ultra series formats: hooks, title formulas, hashtags and script scaffolds.

Every Short belongs to one series so viewers know what to expect and analytics can
compare formats. Templates are deliberately varied (several hooks per series) so
uploads do not read as mass-produced.
"""
from __future__ import annotations

import hashlib

SERIES: dict[str, dict] = {
    "how_filmed": {
        "name": "How Was This Filmed?",
        "playlist_description": "Camera and editing tricks from viral clips, revealed and recreated step by step.",
        "hooks": [
            "This isn't CGI. Here's how they did it.",
            "One camera, zero effects. Watch closely.",
            "Everyone thinks this is edited. It isn't.",
        ],
        "title": "How Was This {subject} Filmed?",
        "category_id": "27",  # Education
        "hashtags": ["#howitsmade", "#filmmakingtricks", "#viralexplained"],
    },
    "meme_origins": {
        "name": "Meme Origins",
        "playlist_description": "Where trending memes and sounds actually came from.",
        "hooks": [
            "Everyone's using this sound. Nobody knows where it's from.",
            "This meme is older than you think.",
            "The original version of this meme is wild.",
        ],
        "title": "The Real Origin of the {subject} Meme",
        "category_id": "24",  # Entertainment
        "hashtags": ["#memeorigins", "#internethistory", "#viralexplained"],
    },
    "missed_this": {
        "name": "You Missed This",
        "playlist_description": "Hidden details in viral clips that most viewers never notice.",
        "hooks": [
            "You watched this 10 times and missed the best part.",
            "Look at the left side of the frame.",
            "There's a second story happening in this clip.",
        ],
        "title": "You Missed This in the Viral {subject} Clip",
        "category_id": "24",
        "hashtags": ["#hiddendetails", "#didyounotice", "#viralexplained"],
    },
    "real_or_fake": {
        "name": "Real or Fake?",
        "playlist_description": "Frame-by-frame verdicts on viral videos people argue about.",
        "hooks": [
            "Millions believed this. Look at this frame.",
            "Real or fake? The shadows give it away.",
            "Before you share this, watch the reflection.",
        ],
        "title": "Is the Viral {subject} Video Real?",
        "category_id": "27",
        "hashtags": ["#realorfake", "#factcheck", "#viralexplained"],
    },
    "trend_30s": {
        "name": "Trend in 30s",
        "playlist_description": "Today's trending internet moment, explained in 30 seconds.",
        "hooks": [
            "If you're confused about {subject}, here's the 30-second version.",
            "Everyone's talking about {subject}. Here's why.",
            "{subject}, explained before your next scroll.",
        ],
        "title": "{subject} Explained in 30 Seconds",
        "category_id": "24",
        "hashtags": ["#trending", "#explained", "#viralexplained"],
    },
}

TITLE_LIMIT = 50
MAX_HASHTAGS = 5


def get_series(key: str) -> dict:
    key = (key or "").strip().lower()
    if key not in SERIES:
        raise ValueError(f"Unknown series '{key}'. Use one of: {', '.join(SERIES)}")
    return SERIES[key]


def _pick(options: list[str], seed: str) -> str:
    """Stable but varied choice per asset, so reruns are reproducible."""
    index = int(hashlib.sha1(seed.encode("utf-8")).hexdigest(), 16) % len(options)
    return options[index]


def build_title(series_key: str, subject: str, override: str = "") -> tuple[str, list[str]]:
    """Return (title, warnings). Keyword-first, aim for <= 50 characters."""
    title = (override or get_series(series_key)["title"].format(subject=subject.strip())).strip()
    warnings = []
    if len(title) > TITLE_LIMIT:
        warnings.append(f"Title is {len(title)} chars; keep it under {TITLE_LIMIT} so it isn't cut off.")
    return title[:100], warnings


def build_hashtags(series_key: str, extra: str = "") -> list[str]:
    """Topic tags first, then series tags; 3-5 total. Over 60 makes YouTube ignore all."""
    tags: list[str] = []
    for raw in (extra or "").replace(",", " ").split():
        tag = "#" + raw.lstrip("#").lower()
        if len(tag) > 1 and tag not in tags:
            tags.append(tag)
    for tag in get_series(series_key)["hashtags"]:
        if tag not in tags:
            tags.append(tag)
    return tags[:MAX_HASHTAGS]


def build_description(asset: dict) -> str:
    """Playbook template: one-line value statement, source credit, 3-5 hashtags."""
    series_key = asset.get("series", "")
    lines = [(asset.get("description") or "").strip()]
    owner = (asset.get("owner") or "").strip()
    url = (asset.get("source_video_url") or "").strip()
    if owner or url:
        credit = "Original clip: " + " - ".join(x for x in (owner, url) if x)
        lines += ["", credit]
    lines.append("Breakdown, voiceover and edit by EzzyClips Ultra.")
    if series_key:
        lines += ["", " ".join(build_hashtags(series_key, asset.get("hashtags", "")))]
    return "\n".join(lines).strip()


def script_scaffold(asset: dict) -> dict:
    """Second-by-second script outline for the editor or an LLM to fill in."""
    series_key = asset.get("series", "")
    series = get_series(series_key)
    subject = (asset.get("subject") or asset.get("title") or "this clip").strip()
    hook = _pick(series["hooks"], asset.get("asset_id", subject)).format(subject=subject)
    title, warnings = build_title(series_key, subject, asset.get("title", ""))
    return {
        "series": series["name"],
        "title": title,
        "title_warnings": warnings,
        "beats": [
            {"time": "0-1.5s", "beat": "Hook (on screen + spoken)", "text": hook},
            {"time": "1.5-5s", "beat": "Stakes", "text": f"State the question this Short answers about {subject}."},
            {"time": "5-30s", "beat": "Breakdown", "text": "Freeze, zoom, slow-mo, arrows or recreation; change something every 1.5-3s. Your narration covers at least half the runtime."},
            {"time": "last 2-3s", "beat": "Payoff + loop", "text": "Reveal the answer, say 'Now watch it again', cut to the first frame."},
        ],
        "comment_prompt": "Did you catch it the first time?",
        "hashtags": build_hashtags(series_key, asset.get("hashtags", "")),
    }
