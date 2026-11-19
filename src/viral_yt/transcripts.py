"""SRT helpers and optional offline transcription of authorized local media."""
from __future__ import annotations

import re
from pathlib import Path


def seconds_to_srt(value: float) -> str:
    msec = int(round(value * 1000))
    hours, rest = divmod(msec, 3600000)
    minutes, rest = divmod(rest, 60000)
    seconds, milliseconds = divmod(rest, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02},{milliseconds:03}"


def srt_to_seconds(value: str) -> float:
    match = re.fullmatch(r"(\d+):(\d{2}):(\d{2})[,.](\d{3})", value.strip())
    if not match:
        raise ValueError(f"Bad SRT timestamp: {value}")
    h, m, s, ms = map(int, match.groups())
    return h * 3600 + m * 60 + s + ms / 1000


def parse_srt(content: str) -> list[tuple[float, float, str]]:
    blocks = re.split(r"\n\s*\n", content.replace("\r\n", "\n").strip())
    results = []
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        time_line_index = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if time_line_index is None:
            continue
        left, right = lines[time_line_index].split("-->", 1)
        right = right.strip().split()[0]
        start, end = srt_to_seconds(left), srt_to_seconds(right)
        words = " ".join(lines[time_line_index + 1:])
        if start < end and words:
            results.append((start, end, words))
    return results


def select_highlight(cues: list[tuple[float, float, str]], max_seconds: int = 50) -> tuple[float, float, str]:
    """Heuristic; require editorial review to ensure the selected passage has context."""
    if not cues:
        raise ValueError("No caption cues to rank")
    hooks = ("how", "why", "secret", "revealed", "surprise", "biggest", "actually", "because", "first", "important", "never")
    best: tuple[float, float, str] | None = None
    best_score = float("-inf")
    for i, (start, _, _) in enumerate(cues):
        batch = [cue for cue in cues[i:] if cue[1] - start <= max_seconds]
        if not batch:
            continue
        end = batch[-1][1]
        text = " ".join(c[2] for c in batch)
        score = sum(1.7 for word in hooks if word in text.lower()) + text.count("?") * 2 + min(len(text.split()), 100) * .02
        score -= abs((end - start) - 36) * .12
        if end - start >= 6 and score > best_score:
            best_score, best = score, (start, end, text)
    if best is None:
        raise ValueError("Captions did not include a 6-second passage")
    return best


def transcribe_owned_file(video_path: Path, output_srt: Path, model_size: str = "small") -> Path:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError("Install optional requirements: pip install -r requirements-optional.txt") from exc
    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    segments, _ = model.transcribe(str(video_path), beam_size=5)
    output_srt.parent.mkdir(parents=True, exist_ok=True)
    with output_srt.open("w", encoding="utf-8") as f:
        for i, segment in enumerate(segments, 1):
            f.write(f"{i}\n{seconds_to_srt(segment.start)} --> {seconds_to_srt(segment.end)}\n{segment.text.strip()}\n\n")
    return output_srt
