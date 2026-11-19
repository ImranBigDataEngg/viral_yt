# TrendTide 🌊

**Trend radar + original / permissioned YouTube Shorts studio — monetization-aware, near-zero cost, Windows-friendly.**

Finds what's trending (official YouTube API) → writes original Short briefs → renders vertical Shorts with voice-over
and animated captions, *or* cuts multiple highlight clips from long-form you own or have permission for →
uploads to your channel (private by default) → exports a download gallery + CSV for TikTok/Instagram.

> Read **[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)** (why v2 doesn't repost other creators' videos, and what it
> automates instead). Brand kit: [docs/CHANNEL_KIT.md](docs/CHANNEL_KIT.md). Free tools & connectors:
> [docs/TOOLS_AND_CONNECTORS.md](docs/TOOLS_AND_CONNECTORS.md).

## Quick start (Windows, `D:\auto_viral_YT`)

```powershell
cd D:\auto_viral_YT
py scripts\install.py --ai --web --voice                     # pure-Python installer, verifies downloads
notepad .env                                   # paste YOUTUBE_API_KEY
.\.venv\Scripts\python.exe .\scripts\demo_synthetic.py    # offline end-to-end demo, no keys
.\.venv\Scripts\trendtide.exe discover         # ranked trends -> output\trends\latest_trends.csv
.\.venv\Scripts\trendtide.exe brief            # original briefs -> output\briefs\*.json
```

Edit a brief (fix every `[VERIFY]`/`[FILL]`, add your take, `"approved": true`), then:

```powershell
.\.venv\Scripts\trendtide.exe compose --brief <brief_id>        # -> output\shorts\<id>.mp4
.\.venv\Scripts\trendtide.exe manifest                         # -> output\manifests\index.html + CSV
.\.venv\Scripts\trendtide.exe upload --id <id>                 # dry-run: shows exact metadata
.\.venv\Scripts\trendtide.exe upload --id <id> --execute       # private upload (OAuth in browser once)
.\.venv\Scripts\trendtide.exe run                              # whole daily cycle (safe defaults)
py scripts\schedule_daily.py --create                         # Task Scheduler, daily 9:17
```

### Lane B — clip long-form you own or have permission for
Add a row to `media\authorized\assets.csv` (see `examples\assets_template.csv`): file path, `rights_basis`
(`original|licensed|permission|cc_by|public_domain`), evidence, owner credit, transcript (`trendtide transcribe`),
`clip_ranges=auto` (or `12-48;95-140`), your voice-over WAV for third-party footage, `approved=true`.
`trendtide render --asset-id X` cuts up to 3 self-contained highlights per source.

### Clip engine v2.1 (compared with artbyjazi/autoclip → [docs/AUTOCLIP_COMPARISON.md](docs/AUTOCLIP_COMPARISON.md))
Hybrid highlight picking (local heuristic + optional one-call LLM judge, 63–97 % fewer tokens), cuts inside measured
silences, shot-aware smart reframe (YuNet faces → locked crop per shot; gameplay/two-shots → blur-fit), cold open
with the best quote, 4 caption styles, GPU encoder auto-probe (NVENC/QSV/AMF/VideoToolbox), parallel renders,
resumable cache. `trendtide doctor` checks everything.

### v2.2: web app, manual clipper, review editor → [docs/WEB_APP.md](docs/WEB_APP.md)
- **Manual clipper mode:** `trendtide clip-url <link|file>` or the web dashboard. YouTube videos and finished live streams
  from channels you verified, Creative Commons videos, direct links and uploads become N clips with hooks and captions.
- **Active-speaker framing:** with two or more people on screen, the crop hard-cuts to whoever is talking
  (mouth motion during speech, hysteresis).
- **9:16, 1:1 and 16:9** exports (`--ratios 9:16,1:1,16:9`).
- **Browser review editor:** trim handles, caption text and timing edits, hook, style and cold open, then the final render.
- **Professional web app** with Stripe subscriptions ($2/day, $25/month, $250/year) and a hardened security model.

## Commands
| Command | What it does |
|---|---|
| `discover [--lanes search,chart,cc]` | Ranked candidates per category (+ CC-BY clip candidates) |
| `brief` | Original Short briefs from the latest trends |
| `compose --brief ID [--force]` | Render an approved explainer (force = local preview only) |
| `transcribe / suggest-highlights / validate / render` | Lane B tools |
| `upload --id ID [--execute] [--publish \| --publish-at ISO]` | Gated upload |
| `share --id ID` | Google Drive link (enable in `config/settings.json`) |
| `manifest` | CSV + HTML gallery with TikTok/IG captions |
| `run [--execute] [--share] [--no-discover]` | Full cycle |
| `doctor [--fetch-models]` | Environment check with exact fixes; downloads the face model |
| `clip-url SOURCE [--count 5] [--ratios 9:16,1:1] [--basis … --evidence …]` | Manual clipper: link/file to clips |
| `verify-channel` | Record which YouTube channels you own (read-only OAuth) |
| `web` / `worker` / `stripe-setup` | Run the SaaS, its job worker, create Stripe prices |

## Status
Implemented & tested locally (31 tests incl. 6 end-to-end web/security tests + FFmpeg demo). Needs your credentials: discovery, upload, Drive.
Public API uploads need YouTube's API audit. Channel creation is manual. TikTok/IG posting is manual from the gallery.

## Safety & monetization
Rights ledger, originality score ≥ 70, per-item approval, daily cap 3, private by default, `containsSyntheticMedia`
flag, secrets in `.secrets/` (gitignored). No earnings are guaranteed — see the plan's monetization section.
