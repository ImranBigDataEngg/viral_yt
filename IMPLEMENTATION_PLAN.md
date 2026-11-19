# TrendTide — Implementation Plan v2 (supersedes v1 "TrendTide 60")

**Workspace:** `D:\auto_viral_YT\` · **Repo:** https://github.com/ImranBigDataEngg/viral_yt · **Revised:** 2026-11-19
**Goal:** a near-zero-cost system that spots what's trending, turns it into *original* or *permissioned* Shorts,
uploads them to your channel, and exports a download pack for TikTok/Instagram — while staying monetizable.

---

## 0. The one decision everything else depends on

**We do not download other creators' viral videos, clip them and re-upload them.** It's not a style choice:

| What happens with "auto-clip + repost" | Why |
|---|---|
| Copyright claims → strikes → channel deleted (3 strikes) | Public ≠ licensed. Credit, cropping, speed changes don't create rights. |
| Demonetized even without claims | YouTube's July 15 2025 update renamed "repetitious" to **"inauthentic content"** and targets mass-produced / reused uploads. |
| Upload API refuses to make it public | Uploads from an API project that hasn't passed YouTube's **compliance audit** are locked **private**; the audit reviews ToS compliance. A repost bot won't pass. |
| Downloading itself breaches YouTube's Terms | Only YouTube-provided features or rights-holder permission allow downloads. |

**What we automate instead (same "trend → clips → upload → cross-post" loop, legally):**

```
          ┌───────────── TREND RADAR (official API, read-only) ─────────────┐
          │ search lane · chart lane (mostPopular) · CC-BY lane · outliers   │
          └───────────────┬──────────────────────────────┬──────────────────┘
                          ▼                              ▼
          LANE A — ORIGINAL EXPLAINERS          LANE B — PERMISSIONED CLIPPING
          trend → brief (script w/ [VERIFY])    long-form you OWN, or with written
          → you fact-check & approve            PERMISSION / licence / CC BY / public domain
          → voice (yours or Piper TTS)          → transcript → auto highlight finder
          → captions + motion bg / own b-roll   → 9:16 blur-fit + captions + YOUR voice-over
                          └──────────────┬───────────────┘
                                         ▼
                ORIGINALITY GATE (score ≥ 70) + per-item approval + daily cap (3)
                                         ▼
               YouTube upload (private → scheduled/public) · Drive links · gallery + CSV
                                         ▼
                        Analytics loop (Studio / Windsor.ai YouTube connector)
```

Lane B is the honest version of "clip long videos into viral Shorts". Good permissioned sources:
your own streams/gameplay recordings (check the game publisher's video policy), podcasts/streamers who
**explicitly invite clippers** (many run paid clipping campaigns — get the terms in writing), YouTube videos
uploaded under **CC BY** (found automatically by the `cc` lane; obtain the file from the creator or
a source they provide, credit them), and public-domain footage (e.g. most NASA media — check each item).

---

## 1. Drawbacks found in v1 and how v2 fixes them

| # | v1 drawback | Impact | v2 fix (status) |
|---|---|---|---|
| 1 | Name "TrendTide 60": the number is ambiguous to say/search ("sixty" vs "60") and locks format to 60 s | Weaker brand recall & search | **TrendTide** (`@TrendTide`), fallbacks `@TrendTideHQ`, `@TrendTideShorts`, `@GetTrendTide` ✅ |
| 2 | Quota facts outdated (said search = 100 units) | Wrong capacity planning | Since **Jun 1 2026** `search.list` and `videos.insert` have their own quota buckets; upload cost fell from ~1,600 to ~100 units (Dec 2025). Planner logs per-run call counts ✅ |
| 3 | Assumed `--publish` works | Uploads silently locked private | Plan step: **apply for the API compliance audit in week 1**; until approved, upload private and publish manually in Studio / use the gallery for manual upload ✅ |
| 4 | No synthetic-media flag | Policy gap | `status.containsSyntheticMedia` set from brief/ledger ✅ |
| 5 | No content engine — user had to hand-make everything | Not "automated" | Brief generator (template or local Ollama), Piper/flite TTS, word-timed captions, motion background composer ✅ |
| 6 | Single highlight, keyword-only scoring, could cut mid-sentence | Weak clips | Multi-clip DP selector: sentence-bounded, complete-thought endings, pacing, hook words, audio-energy peaks ✅ |
| 7 | Center-crop only | 16:9 gameplay loses HUD/text | `blurfit` layout (full frame over blurred fill) + `crop` ✅ |
| 8 | Commentary *replaced* source audio | Unnatural | `duck` mix: source at −13 dB under your voice; EBU R128 loudness −14 LUFS ✅ |
| 9 | Static small subtitles | Lower retention | Animated 1–3-word captions, active-word highlight, hook title card, credit line ✅ |
| 10 | No state → duplicate work, no caps | Re-renders, spam risk | SQLite state: seen candidates, renders, uploads, shares; idempotent; daily upload cap ✅ |
| 11 | Discovery: one lane, no outlier signal | Misses breakouts | 3 lanes + outlier ratio (views ÷ channel subs) + `angle_hint` for an original take ✅ |
| 12 | Monetization gate = 2 booleans | Reused content could pass | **Originality score** (voice, footage ownership, transformation, duplicate titles) must be ≥ 70 to go public ✅ |
| 13 | Only `file:///` links | Can't download on phone | Optional Google Drive (`drive.file` scope) share links + offline HTML gallery with copy-ready TikTok/IG captions ✅ |
| 14 | Schedules only discovery; job missed if PC asleep | Gaps | `trendtide run` full cycle; Task Scheduler with `StartWhenAvailable`/`WakeToRun` ✅ |
| 15 | Revenue section outdated | Wrong expectations | Updated YPP thresholds incl. **Feb 1 2027** changes (§7) → long-form added to strategy ✅ |
| 16 | No CI | Regressions | GitHub Actions runs tests + FFmpeg smoke render ✅ |

---

### 1b. v2.1 clip-engine upgrade (after reviewing artbyjazi/autoclip)
Hybrid heuristic + one-call LLM judging (63–97 % fewer tokens than whole-transcript windows), silence-aligned cuts,
shot-aware YuNet smart reframe, cold open, 4 caption styles, multi-vendor GPU encoder probe, parallel renders,
content-addressed stage cache, `doctor`. Details, benchmark and the honest "where autoclip is still ahead" list:
[docs/AUTOCLIP_COMPARISON.md](docs/AUTOCLIP_COMPARISON.md).

## 2. Brand & channel (manual — needs your Google account)

- **Name:** TrendTide · **Handle:** `@TrendTide` (check in YouTube; fallbacks above) · **Tagline:** *The story behind what's trending.*
- Why: two short common words, alliterative, instantly says "trends", no numbers/odd spelling, works on TikTok/IG too.
- Setup checklist and brand kit: [`docs/CHANNEL_KIT.md`](docs/CHANNEL_KIT.md). Channel creation, handle, verification
  (phone — unlocks custom thumbnails/longer videos), 2FA and **Brand Account** (so you can add managers later) are done
  by you in YouTube; no API can create a channel.

## 3. Lanes & modules (all implemented in `src/viral_yt/`)

| Module | Does |
|---|---|
| `discovery.py` | search / chart / CC lanes, enrichment, outlier ratio, dedupe, CSV+JSON, call counter |
| `scriptgen.py` | brief per top candidate: 3 hooks, structured script, titles, tags, `[VERIFY]` blockers |
| `tts.py` | Piper (natural, local) → FFmpeg flite fallback; your own WAV preferred |
| `captions.py` | ASS animated captions, hook card, credit |
| `composer.py` | explainer render (voice + captions + own/licensed b-roll or generated motion bg) |
| `highlights.py` | multi-clip selection from transcript + audio energy |
| `clipper.py` | permissioned clip render, layouts, ducking, loudness, sidecar |
| `rights.py` | ledger validation: basis ∈ original/licensed/permission/cc_by/public_domain + evidence + credit + voice-over for 3rd-party |
| `originality.py` | 0–100 monetization-readiness score with reasons |
| `publisher.py` | dry-run → private → scheduled/public; tags, credits, synthetic flag, cap, idempotent |
| `share.py` | Google Drive links (optional) |
| `manifest.py` | download CSV + HTML gallery with platform captions |
| `pipeline.py` / `cli.py` | `trendtide run` and per-step commands |

## 4. Daily operating rhythm (≈30–45 min of human time)

1. **Auto 09:17** — `run`: discover → briefs (3/category) → render anything approved → manifest.
2. **You (20 min)** — open `output/briefs/*.json`, pick 2–3, verify facts, add your opinion, set `"approved": true`.
   Optionally record your voice (phone is fine) → `voice_file`.
3. **You (10 min)** — for Lane B, add permission evidence + `approved=true` in `media/authorized/assets.csv`.
4. `trendtide run --no-discover --execute` renders and uploads **private**; review in Studio; set `publish_approved`
   and `trendtide upload --id X --execute --publish-at 2026-11-21T14:00:00Z` (after audit) or publish in Studio.
5. Open `output/manifests/index.html` on your PC/phone → download MP4 + copy caption → post to TikTok/IG
   (upload natively, no watermark; add trending audio *inside* each app from its licensed library).

## 5. Quota & cost (verify in Google Cloud Console → Quotas)

- Default project quota 10,000 units/day; search & upload now billed in separate buckets (Jun 2026).
  Default config: 5 categories × ~2–3 terms × 2 search lanes ≈ 25–30 search calls + ~5 chart + a few enrich/channel calls.
- Everything else is free and local: FFmpeg, Python, Piper, faster-whisper, Ollama. Drive: 15 GB free.

## 6. Status (be honest about what is proven)

| Item | Status |
|---|---|
| All modules above, CLI, tests (18), synthetic end-to-end demo, smart reframe verified on a 3-shot test video | **Implemented & tested locally** (Linux, FFmpeg 7) |
| Windows install / scheduler scripts | Implemented — **needs run on your PC** |
| Live discovery | Needs your `YOUTUBE_API_KEY` |
| Upload / Drive share | Implemented — **needs your OAuth client + live verification** |
| Public uploads via API | **Blocked until API audit approved** |
| Channel + handle | **Manual — not created by this system** |
| TikTok / Instagram auto-post | Not implemented (export pack instead). TikTok Content Posting API and Instagram Graph API (Reels, Business account) both require app review — add in phase 3 if volume justifies |

## 7. Monetization reality (checked Nov 2026)

- **Today:** fan funding/Shopping tier = 500 subs + 3 public uploads/90 d + 3,000 watch h (12 mo) **or** 3 M Shorts views (90 d).
  Ads = 1,000 subs + 4,000 h **or** 10 M Shorts views (90 d).
- **From Feb 1 2027 (new applicants):** 8,000 h **or** 20 M Shorts views; and *every* channel needs 10 M Shorts views in
  the trailing 90 days to receive Shorts-pool revenue that month.
- **Implication:** a Shorts-only channel will very likely not earn ad revenue in year one. v2 strategy:
  Shorts for discovery → **one 6–10 min long-form explainer per week** (watch hours) → Tier-1 features (memberships,
  Shopping/affiliate links with disclosure) → brand deals. No income is guaranteed.

## 8. Roadmap

- **Week 1:** create channel & brand kit; Google Cloud project, API key, OAuth client; **submit API audit**; run demo;
  first 5 Shorts (Lane A) + 1 long-form.
- **Weeks 2–4:** 1–2 Shorts/day max (cap 3); collect 3–5 permissioned sources for Lane B; A/B hooks; kill weak categories.
- **Month 2–3:** weekly long-form; optional Ollama briefs, Drive links, Windsor.ai analytics review; consider
  official TikTok/IG APIs.

## 9. Risk register

| Risk | Mitigation |
|---|---|
| Strikes / claims | Rights ledger + evidence files; no third-party media without basis; music only from YouTube Audio Library / in-app libraries |
| "Inauthentic content" demonetization | Originality gate, daily cap, human approval, own voice preferred, varied formats |
| Misinformation / defamation | `[VERIFY]` blockers, sources in description, no rumours as facts ("Myth vs Confirmed" format) |
| API audit delay | Private uploads + manual publish meanwhile |
| Token leak | `.secrets/` gitignored, narrow scopes (`youtube.upload`, `drive.file`), revoke at myaccount.google.com/permissions |
| PC off at schedule | `StartWhenAvailable`; or later an always-on box (old laptop, or a free-tier cloud VM) |

## References
YouTube monetization policies (support.google.com/youtube/answer/1311392) · YPP (answer/72851) ·
Data API revision history (developers.google.com/youtube/v3/revision_history) · videos.insert ·
search.list `videoLicense` · Creative Commons on YouTube (answer/2797468).
