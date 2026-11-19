# TrendTide clip engine v2.1 vs artbyjazi/autoclip

Reviewed 2026-11-19 (autoclip `main`, MIT, ~8.8k lines Python + React UI). autoclip is a strong, well-engineered
local clipper; this is what we learned from it, what we adopted (re-implemented, not copied), and where TrendTide
now goes further.

## What autoclip does well (and what we did with it)

| autoclip idea | TrendTide v2.1 |
|---|---|
| LLM cites **word indices**, never seconds | Adopted, at sentence granularity: the model cites `[s<idx>]` tags; timing comes from measured word times |
| Validate JSON → **retry once with the error** | Adopted (`llm.rerank`), plus automatic fallback to the local heuristic if the model still fails |
| Overlapping 8-min windows over the **whole** transcript | **Replaced** by a hybrid: local heuristic shortlists ≤16 diverse windows, the LLM judges only those in ONE call (see benchmark) |
| Sentence snap + **cut inside measured silences** | Adopted (`media_prep.align_cut`, ffmpeg `silencedetect`, cached) |
| Shot detection; **never pan across a cut** | Adopted with zero extra deps: one low-res decode gives FFmpeg `scdet` cuts *and* sample frames |
| Face tracking (MediaPipe) + One-Euro smoothing | Replaced by **one locked framing per shot** (YuNet face detector, 230 KB, works on Python 3.13); two-shots, no-face shots and gameplay → blur-fit |
| 4 caption styles | Adopted: `bold_pop`, `karaoke_fill` (`\kf`), `boxed`, `clean_lower` |
| Functional GPU-encoder probe (listing ≠ working) | Adopted and widened: NVENC **+ Intel QSV + AMD AMF + VideoToolbox**, with mid-render CPU fallback |
| Stage artifacts → resume | Adopted as a **content-addressed cache** (`output/work/<fingerprint>/`: audio, silences, words, highlights) |
| `autoclip doctor` | Adopted: `trendtide doctor` (+ `--fetch-models`) |
| cwd trick so filtergraphs never need path escaping | Already used in v2 |

## Where TrendTide is ahead

| Area | autoclip | TrendTide v2.1 |
|---|---|---|
| LLM tokens per video (benchmark below) | grows linearly with length (whole transcript, every word index-tagged, overlap) | ~flat ≈2.5–2.7k tokens, **63–97 % fewer**, 1 call instead of 2–17 |
| Works without any LLM | No — selection needs a model | Yes — DP heuristic (hook language, complete-thought endings, pacing, audio-energy peaks); LLM optional |
| Cold open | — | Best quotable line (1.5–5 s) played first, framed like its source shot, captions re-timed |
| Parallel clip renders | sequential | `clips.workers` thread pool |
| Blur-fit cost | — | background blurred at 1/6 resolution then upscaled (demo render 56.5 s → 34.7 s for 3 clips, incl. 2 workers) |
| Python 3.13 / install weight | 3.11–3.12 only (MediaPipe); Node build; optional PyTorch for diarization | 3.11–3.13; no Node, no PyTorch; OpenCV + 230 KB model optional |
| Trend discovery | — | Official-API radar (search / chart / CC-BY lanes, outlier ratio) |
| Original content lane | — | Script briefs → TTS → captions → explainer Shorts |
| Rights & monetization safety | "only download what you own" note; bundles yt-dlp with cookie support | Rights ledger with evidence, credit lines, voice-over required for third-party, originality score ≥ 70 gate, daily cap |
| Publishing | Export only (non-goal) | YouTube upload (private/scheduled/public, `containsSyntheticMedia`), Drive links, TikTok/IG caption gallery |

## Where autoclip is still ahead (honest list → roadmap)

1. **Review UI** (React: player, trim handles, caption editor, live job progress). TrendTide has a static gallery.
   → Roadmap: small FastAPI + HTMX review page (fits your stack).
2. **Active-speaker framing** in multi-person shots (WhisperX diarization + mouth-aspect-ratio). TrendTide falls back
   to blur-fit for two-shots. → Roadmap: YuNet landmarks give mouth points; add mouth-motion speaker pick.
3. **1:1 and 16:9 exports.** TrendTide is 9:16 only (all target platforms). → Trivial to add if needed.
4. **Native Anthropic / Gemini adapters** (we use one OpenAI-compatible adapter + Ollama, which covers Gemini, Groq,
   OpenRouter, DeepSeek, LM Studio).
5. **Docker image.**
6. Maturity: more tests around reframe edge cases.

Deliberately **not** adopted: yt-dlp ingest with browser cookies. TrendTide does not download third-party videos;
sources come from your own files or rights holders (see IMPLEMENTATION_PLAN §0).

## Benchmark (scripts/benchmark_selection.py — synthetic transcript, 2.6 words/s)

```
  video | windowed tokens calls | hybrid tokens calls | saving
   10 m |           6,690     2 |         2,496     1 |  62.7%
   30 m |          19,770     5 |         2,557     1 |  87.1%
   60 m |          38,708     9 |         2,742     1 |  92.9%
  120 m |          78,665    17 |         2,656     1 |  96.6%
```
"Windowed" re-creates autoclip's input format (8-min windows, 60-s overlap, `[index]word` tagging, ~1.1k-token
prompt per window). Trade-off: the LLM only sees what the heuristic shortlists, so a highlight the heuristic misses
entirely can't be rescued. The shortlist is deliberately wide (≤16 non-overlapping windows) to keep recall high.
