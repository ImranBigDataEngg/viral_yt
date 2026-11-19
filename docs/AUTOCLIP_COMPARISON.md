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
# Technical Comparison: AutoClip vs TrendTide 60

**Prepared:** 2026-11-19  
**External project:** https://github.com/artbyjazi/autoclip  
**Our project:** https://github.com/ImranBigDataEngg/viral_yt  
**Method:** Source-code and documentation inspection of both repositories' `main` branches on the date above. **No head-to-head performance test or AutoClip local installation was performed.**

## Executive decision

**Keep TrendTide 60 as the content research, asset-rights, editorial, distribution, and YouTube publishing layer. Evaluate AutoClip as the specialized AI-assisted clipping/editing layer.** AutoClip already solves several complex media-editing problems that we should not rebuild immediately. Neither repository can guarantee viral performance, copyright clearance, or monetization eligibility.

## Scope and feature comparison

| Capability | AutoClip | TrendTide 60 (`viral_yt`) | Practical advantage |
|---|---|---|---|
| Trending topic discovery on YouTube | Not provided | Topic-based YouTube Data API search, views/hour engagement ranking, top-N CSV | TrendTide 60 |
| Search for top 10 per category | Not provided | Configured category terms, `--top 10`, trend CSV/JSON | TrendTide 60 |
| Input | Local media; YouTube URL via yt-dlp | Approved local video from rights ledger | AutoClip for convenience; TrendTide for restrictions |
| Transcription | faster-whisper with word-level timestamps; optional WhisperX diarization | Optional faster-whisper to segment-level SRT | AutoClip |
| AI moment selection | Transcript-window LLM analysis, structured candidates, ranking, dedupe | Heuristic hook-keyword scoring for one suggested passage | AutoClip |
| Boundaries | Word indices; sentence/silence alignment and duration bounds | SRT cue timestamps and selected start/end | AutoClip |
| Automated 9:16 reframing | Scene/face/speaker tracking, smoothing, centre fallback | Static centre crop | AutoClip for interviews/talking heads |
| Captions | Four ASS styles; per-word highlighting and karaoke | SRT with static FFmpeg burn-in | AutoClip |
| Output | 9:16, 1:1, 16:9 MP4 | 1080x1920 MP4 | AutoClip for formats |
| Audio finishing | Configurable normalization; README claims -14 LUFS E2E tested | AAC encoding; no loudness normalization | AutoClip |
| Editing and QA UI | React review workflow, trim bar, caption edits, preview | CLI and file-based ledger | AutoClip |
| Job state/recovery | SQLite queue; resumable stage artifacts and progress events | Individual commands; no persistent job database | AutoClip |
| Rights/approval ledger | Documentation warns to use only permitted videos; no comparable per-asset release ledger found | Explicit basis/evidence, `approved`, `publish_approved` guards | TrendTide 60 |
| Official YouTube upload | Deliberate non-goal | OAuth-based private upload; public requires explicit approval | TrendTide 60 |
| TikTok/Instagram upload | No | No (local manifest supports manual upload) | Neither |
| Download manifest | UI downloadable exports; not cross-platform CSV ledger | CSV with local `file:///` URIs and platform statuses | TrendTide 60 |
| Automated original script or voiceover generation | Not a core feature | Not automatic; user-supplied commentary audio | Neither |
| Test sophistication | Broad unit, API, render, E2E, CI on Win/macOS/Linux; golden framing gate not yet met | Small unit suite plus synthetic render demo | AutoClip |
| License | MIT; preserve license notices; font assets may carry separate OFL terms | Own repository | AutoClip reusable under conditions |

## Architecture comparison

**AutoClip:** `ingest -> prepare -> transcribe -> highlights -> reframe -> captions -> export`, via FastAPI + React + SQLite + local FIFO work queue. Supports local Ollama or provider keys (Anthropic, OpenAI, Gemini, and OpenAI-compatible services). Word-index LLM outputs avoid timestamp hallucinations. Cuts align to natural sentence and silence boundaries, and framing is recomputed per scene. Exports are local; it intentionally does not publish directly.

**TrendTide 60:** `YouTube category/topic discovery -> human rights/evidence approval -> transcript/highlight suggestion -> FFmpeg render -> CSV manifest -> OAuth YouTube upload with explicit public-publish gate`. It is lightweight and easier to deploy, but the center-crop, static subtitles, heuristics, and lack of editor are the main quality limitations. Trending video **discovery** means research, not permission to copy source material.

## AutoClip limitations and cautions

1. AutoClip is `0.1.0.dev0` / pre-1.0 according to `pyproject.toml` and README. Its own README says shot-framing acceptance has not yet passed a fixed golden video set, and viral-pick quality is not objectively established.
2. Its highlight engine is optimized for speech/transcripts. Gameplay montages, music-driven memes, silent trailers, and text-on-screen events may require scene, motion, audio and OCR-based selection in addition to transcripts.
3. yt-dlp may encounter YouTube authentication/bot restrictions. Even when it downloads a video, **downloadability does not grant reuse, clipping, redistribution, or monetization rights**. Only ingest owned or properly licensed media; do not build around scraping arbitrary top-ten videos.
4. Python must be 3.11 or 3.12 according to the upstream dependency declarations; ffmpeg must include `libass`/`libx264`; Node 20+ is used to build its UI. Optional diarization adds substantial dependencies and model-license obligations.
5. Fully local operation can have zero per-video API fees using Ollama, but uses disk, CPU/GPU and electricity. Commercial LLM calls are optional and may cost money.
6. AutoClip is a local video production tool, not a complete multi-social content business, scheduling, rights clearance or earnings solution.
7. Review code and pin a particular upstream commit before incorporating it; avoid dynamically updating third-party packages during unattended production jobs.

## Concrete weaknesses identified in our current code

1. `src/viral_yt/transcripts.py:select_highlight()` is a simple keyword-based SRT heuristic and emits only one best interval. It cannot reliably detect narrative hooks, self-contained payoffs or multiple distinct clips, nor word-level edit boundaries.
2. `src/viral_yt/clipper.py` uses FFmpeg `scale/crop` with static centered framing, rather than speaker/scene-sensitive cropping; its captions are standard SRT, not word-animated.
3. No graphical editorial review, stage resume, rich job database, audio normalization or automated multi-candidate quality evaluation.
4. `src/viral_yt/manifest.py` iterates `output/shorts/*.json` and assumes every JSON file contains `file`. `src/viral_yt/publisher.py` additionally writes `<asset>.youtube.json` without `file` when an upload succeeds. **Consequently, building a manifest after a successful upload can raise `KeyError: 'file'`.** Fix by excluding `.youtube.json` or validating the metadata schema, and add a regression test.
5. `rights_evidence` is only checked for minimum length; it is not legal verification. Keep human editorial/legal review. `selfDeclaredMadeForKids=False` is hardcoded in the uploader and should become a per-asset reviewed classification.
6. YouTube topic discovery is keyword search with a calculated score, **not** an authoritative YouTube trending chart. Add a distinct `mostPopular` category if useful; be clear about API quota and ranking differences.
7. The OAuth uploader and external API discovery have not been live end-to-end tested with the owner's Google credentials; no real channel publishing has been verified.

## Integration plan (recommended)

### Step 0 — Fix and test the current pipeline

- Patch the manifest sidecar issue with a regression test.
- Use Python 3.11 or 3.12 in a dedicated AutoClip environment to avoid version conflicts; use FFmpeg full build.
- Preserve the existing asset approval/publish approval protections.

### Step 1 — Add a stable AutoClip adapter (no fork initially)

- Add `src/viral_yt/integrations/autoclip_adapter.py` with a **local-source-only** workflow: `rights.validate_asset()` first, then invoke the pinned AutoClip CLI or local API for the *authorized* source file.
- Set `AUTOCLIP_HOME=D:\\auto_viral_YT\\.autoclip_data` on Windows (gitignored). Do not pass discovered YouTube URLs directly into the clipper.
- Capture structured stdout/status, produced MP4 paths, candidate scores, titles and metadata; map them back to `asset_id` and rights evidence. Copy approved exports into `output/shorts` and update our manifest schema.
- Use a pinned release/commit and keep the upstream MIT license notice. Avoid embedding browser cookies or tokens in commands/logs.

### Step 2 — Editorial quality and brand

- Expose an approval queue that uses AutoClip's review interface or a small TrendTide dashboard.
- Add a branded original-commentary layer, fact checks, on-screen source references, and distinctive content formatting. AutoClip's captions/auto-crop alone do **not** turn reposted material into original content.
- Benchmark 20 approved videos and inspect precision of top-3 highlights, crop stability, caption alignment, export time, and completion success. Include gaming-specific footage that lacks dialogue.

### Step 3 — Distribution and analytics

- Keep YouTube uploads private by default; make public publishing require explicit per-asset review.
- Add a platform-ready manifest and only enable Instagram/TikTok API integration where a permitted official publishing pathway is available for the connected account.
- Add YouTube Analytics retention, average view duration, subscribed-viewer conversion and repeat-viewing metrics. Feedback should change topic selection and editorial decisions, not automatically bypass rights review.

## Prioritization

**P0:** manifest regression fix; rights provenance retained; test on Windows.  
**P1:** AutoClip adapter for authorized local videos; multi-clip selection, captions, scene-aware crop.  
**P2:** human review dashboard, style templates, audio normalization, original voiceover/analysis.  
**P3:** scheduled cross-platform publishing and actual retention analytics.

## Bottom line

For clipping *quality*, AutoClip is currently ahead. For an end-to-end **trending-topic-to-reviewed-YouTube-output** operation, TrendTide supplies complementary research and upload/permission components. Best path: **integrate AutoClip as a replaceable engine**, improve our editorial originality and copyright checks, and keep the distribution layer independent.

### Inspected primary sources

- https://github.com/artbyjazi/autoclip/blob/main/README.md
- https://github.com/artbyjazi/autoclip/blob/main/docs/ARCHITECTURE.md
- https://github.com/artbyjazi/autoclip/blob/main/pyproject.toml
- https://github.com/artbyjazi/autoclip/blob/main/LICENSE
- https://github.com/artbyjazi/autoclip/blob/main/backend/autoclip/pipeline/highlights.py
- https://github.com/artbyjazi/autoclip/blob/main/backend/autoclip/pipeline/reframe/__init__.py
- https://github.com/artbyjazi/autoclip/blob/main/backend/autoclip/pipeline/captions.py
- https://github.com/ImranBigDataEngg/viral_yt/blob/main/src/viral_yt/discovery.py
- https://github.com/ImranBigDataEngg/viral_yt/blob/main/src/viral_yt/clipper.py
- https://github.com/ImranBigDataEngg/viral_yt/blob/main/src/viral_yt/manifest.py
- https://github.com/ImranBigDataEngg/viral_yt/blob/main/src/viral_yt/publisher.py
- https://github.com/ImranBigDataEngg/viral_yt/blob/main/src/viral_yt/rights.py
