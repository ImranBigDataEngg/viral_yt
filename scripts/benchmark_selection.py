"""Compare LLM token load: whole-transcript windowed scanning (every word tagged with its index, 8-min windows,
60-s overlap — the approach used by artbyjazi/autoclip) vs TrendTide's hybrid shortlist (one call, sentence tags)."""
from __future__ import annotations

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from viral_yt.highlights import select_clips  # noqa: E402
from viral_yt.llm import PROMPT, estimate_tokens  # noqa: E402
from viral_yt.media_prep import sentences  # noqa: E402

VOCAB = ("the people really think this matters because every number we saw last year changed how "
         "creators plan videos and nobody expected that result at all").split()


def fake_words(minutes: int, wps: float = 2.6, seed: int = 7):
    rnd, words, t = random.Random(seed), [], 0.0
    while t < minutes * 60:
        n = rnd.randint(8, 18)
        for k in range(n):
            w = rnd.choice(VOCAB) + ("?" if k == n - 1 and rnd.random() < .15 else "." if k == n - 1 else "")
            words.append((t, t + 1 / wps * .85, w))
            t += 1 / wps
        t += rnd.uniform(.2, .7)
    return words


def windowed_tokens(words, window_s=480, overlap_s=60, prompt_tokens=1100):
    total, calls, i = 0, 0, 0
    while i < len(words):
        start = words[i][0]
        j = i
        while j + 1 < len(words) and words[j + 1][1] <= start + window_s:
            j += 1
        text = " ".join(f"[{k}]{words[k][2]}" for k in range(i, j + 1))
        total += estimate_tokens(text) + prompt_tokens
        calls += 1
        if j >= len(words) - 1:
            break
        nxt = words[j][1] - overlap_s
        i = max(i + 1, next(k for k in range(i, len(words)) if words[k][0] >= nxt))
    return total, calls


if __name__ == "__main__":
    print(f"{'video':>7} | {'windowed tokens':>15} {'calls':>5} | {'hybrid tokens':>13} {'calls':>5} | saving")
    for minutes in (10, 30, 60, 120):
        words = fake_words(minutes)
        wt, wc = windowed_tokens(words)
        _, stats = select_clips(sentences(words), 3, 20, 58, use_llm=True,
                                completer=lambda p: '{"clips": []}')
        ht = stats["llm_tokens_est"] + estimate_tokens(PROMPT)
        print(f"{minutes:>5} m | {wt:>15,} {wc:>5} | {ht:>13,} {1:>5} | {100 * (1 - ht / wt):5.1f}%")
