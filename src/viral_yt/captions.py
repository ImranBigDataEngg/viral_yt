"""Shorts-style captions (ASS) with four presets, hook title card and credit line.

Presets: bold_pop (word grows + turns yellow), karaoke_fill (words fill as spoken, \\kf),
boxed (solid block, active word coloured), clean_lower (minimal lower third, static).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Style:
    key: str
    size_ratio: float          # font size as fraction of video height
    primary: str               # ASS &HAABBGGRR
    accent: str
    outline: float
    shadow: float
    border_style: int          # 1 outline, 3 opaque box
    back: str
    y_ratio: float             # caption centre as fraction of height
    max_words: int
    caps: bool
    animation: str             # scale | karaoke | colour | none
    bold: int = 1


STYLES = {
    "bold_pop": Style("bold_pop", 0.041, "&H00FFFFFF", "&H0022D3EE", 7, 3, 1, "&H96000000", 0.62, 3, False, "scale"),
    "karaoke_fill": Style("karaoke_fill", 0.040, "&H00FFFFFF", "&H0081E931", 6, 2, 1, "&H96000000", 0.62, 5, True, "karaoke"),
    "boxed": Style("boxed", 0.036, "&H00FFFFFF", "&H004D4DFF", 14, 0, 3, "&H30000000", 0.64, 4, True, "colour"),
    "clean_lower": Style("clean_lower", 0.026, "&H00FFFFFF", "&H00FFFFFF", 2, 1, 1, "&H80000000", 0.80, 8, False, "none", 0),
}
DEFAULT_STYLE = "bold_pop"


def ass_time(t: float) -> str:
    cs = int(round(max(t, 0) * 100))
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


def _header(w: int, h: int, font: str, st: Style, size_override: int | None) -> str:
    ref = min(w, h) * 16 / 9               # sizes are tuned for 1080x1920; keep them equal on 1:1 and 16:9
    size = size_override or int(ref * st.size_ratio)
    # \kf sweeps PrimaryColour over SecondaryColour: sung words = accent, unsung = white
    primary, secondary = (st.accent, st.primary) if st.animation == "karaoke" else (st.primary, st.primary)
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{font},{size},{primary},{secondary},&H00271811,{st.back},{st.bold},0,0,0,100,100,0,0,{st.border_style},{st.outline},{st.shadow},5,110,110,0,1
Style: Hook,{font},{int(ref * 0.0385)},&H00FFFFFF,&H00FFFFFF,&H10271811,&H10271811,1,0,0,0,100,100,0,0,3,20,0,8,90,90,{int(h * 0.12) if h > w else int(h * 0.07)},1
Style: Credit,{font},{max(26, int(ref * 0.0178))},&H00E5E7EB,&H00FFFFFF,&H00111827,&H80000000,0,0,0,0,100,100,0,0,1,2,1,1,60,160,70,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def build_ass(words, target: Path, width: int = 1080, height: int = 1920, font: str = "Arial", size: int | None = None,
              hook_text: str = "", hook_seconds: float = 2.6, credit: str = "", total: float | None = None,
              max_words: int | None = None, caption_y: int | None = None, style: str = DEFAULT_STYLE) -> Path:
    """words: list of (start, end, word) relative to the output timeline."""
    st = STYLES.get(style, STYLES[DEFAULT_STYLE])
    y = caption_y or int(height * (st.y_ratio if height > width else max(st.y_ratio, 0.80)))
    pos = r"{\an5\pos(%d,%d)}" % (width // 2, y)
    lines = [_header(width, height, font, st, size)]
    if hook_text:
        lines.append(f"Dialogue: 2,{ass_time(0)},{ass_time(hook_seconds)},Hook,,0,0,0,,"
                     r"{\fad(80,200)}" + esc(hook_text.upper()))
    pop = 110 if st.animation == "scale" else 100
    hi = r"{\c%s&\fscx%d\fscy%d}" % (st.accent.replace("&H00", "&H"), pop, pop)
    reset = r"{\c%s&\fscx100\fscy100}" % st.primary.replace("&H00", "&H")
    for group in group_words(words, max_words=max_words or st.max_words):
        txt = [(esc(t).upper() if st.caps else esc(t)) for _, _, t in group]
        g_start, g_end = group[0][0], group[-1][1]
        if st.animation == "none":
            lines.append(f"Dialogue: 1,{ass_time(g_start)},{ass_time(g_end)},Cap,,0,0,0,,{pos}{' '.join(txt)}")
        elif st.animation == "karaoke":
            parts, cursor = [], g_start
            for (ws, we, _), t in zip(group, txt):
                gap = max(0.0, ws - cursor)
                parts.append((r"{\k%d}" % int(round(gap * 100)) if gap > 0.01 else "") +
                             r"{\kf%d}%s" % (max(1, int(round((we - ws) * 100))), t))
                cursor = we
            lines.append(f"Dialogue: 1,{ass_time(g_start)},{ass_time(g_end + 0.15)},Cap,,0,0,0,,{pos}{' '.join(parts)}")
        else:
            for k, (ws, _, _) in enumerate(group):
                end = group[k + 1][0] if k + 1 < len(group) else g_end
                parts = [(hi + t + reset) if n == k else t for n, t in enumerate(txt)]
                lines.append(f"Dialogue: 1,{ass_time(ws)},{ass_time(max(end, ws + 0.05))},Cap,,0,0,0,,{pos}{' '.join(parts)}")
    if credit and total:
        lines.append(f"Dialogue: 0,{ass_time(0)},{ass_time(total)},Credit,,0,0,0,,{esc(credit)}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target
