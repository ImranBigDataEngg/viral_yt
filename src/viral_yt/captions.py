"""Shorts-style animated captions (ASS): 1–3 words at a time, active word highlighted, hook title card."""
from __future__ import annotations

from pathlib import Path

ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{font},{size},&H00FFFFFF,&H0000E5FF,&H00271811,&H96000000,1,0,0,0,100,100,0,0,1,7,3,5,110,110,0,1
Style: Hook,{font},{hook},&H00FFFFFF,&H00FFFFFF,&H10271811,&H10271811,1,0,0,0,100,100,0,0,3,20,0,8,90,90,230,1
Style: Credit,{font},34,&H00E5E7EB,&H00FFFFFF,&H00111827,&H80000000,0,0,0,0,100,100,0,0,1,2,1,1,60,160,70,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
HIGHLIGHT = r"{\c&H0022D3EE&\fscx108\fscy108}"   # ASS colours are BGR: this is warm yellow #EED322 + slight pop
RESET = r"{\c&H00FFFFFF&\fscx100\fscy100}"


def ass_time(t: float) -> str:
    t = max(t, 0)
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02}:{s:02}.{cs:02}"


def esc(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def group_words(words, max_words: int = 3, max_gap: float = 0.6):
    groups, cur = [], []
    for w in words:
        if cur and (len(cur) >= max_words or w[0] - cur[-1][1] > max_gap or cur[-1][2].endswith((".", "?", "!", ","))):
            groups.append(cur)
            cur = []
        cur.append(w)
    if cur:
        groups.append(cur)
    return groups


def build_ass(words, target: Path, width: int = 1080, height: int = 1920, font: str = "Arial", size: int = 78,
              hook_text: str = "", hook_seconds: float = 2.6, credit: str = "", total: float | None = None,
              max_words: int = 3, caption_y: int | None = None) -> Path:
    """words: list of (start, end, word). Captions sit at ~62% height (above Shorts UI, below faces)."""
    y = caption_y or int(height * 0.62)
    lines = [ASS_HEADER.format(w=width, h=height, font=font, size=size, hook=int(size * 0.95))]
    if hook_text:
        lines.append(f"Dialogue: 2,{ass_time(0)},{ass_time(hook_seconds)},Hook,,0,0,0,,"
                     r"{\fad(80,200)}" + esc(hook_text.upper()))
    for group in group_words(words, max_words=max_words):
        g_end = group[-1][1]
        for k, (ws, we, _) in enumerate(group):
            end = group[k + 1][0] if k + 1 < len(group) else g_end
            parts = [(HIGHLIGHT + esc(t) + RESET) if n == k else esc(t) for n, (_, _, t) in enumerate(group)]
            lines.append(f"Dialogue: 1,{ass_time(ws)},{ass_time(max(end, ws + 0.05))},Cap,,0,0,0,,"
                         + r"{\an5\pos(%d,%d)}" % (width // 2, y) + " ".join(parts))
    if credit and total:
        lines.append(f"Dialogue: 0,{ass_time(0)},{ass_time(total)},Credit,,0,0,0,,{esc(credit)}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target
