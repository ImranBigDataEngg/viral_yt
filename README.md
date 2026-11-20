# TrendTide 60 🎬

**Viral-topic research + original YouTube Shorts production, with copyright and publication safeguards.**

This is a usable starter application, not a completed YouTube channel. It discovers ranked topics with Google's official YouTube API, renders **locally owned or permission-cleared** videos with FFmpeg, generates a downloadable clip manifest, and supports OAuth uploads to the owner's YouTube account. It never downloads arbitrary YouTube videos or scrapes third-party transcripts.

> Read [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) first. A reusable build-agent prompt lives in [MASTER_PROMPT.md](MASTER_PROMPT.md).

## What works

| Feature | Status |
|---|---|
| 5+ topic groups, including GTA 6 | Implemented: configurable |
| Up to 10 ranked public YouTube *ideas* per category | Implemented: live key needed |
| Save trend insights as CSV/JSON | Implemented |
| Process documented original / licensed local files | Implemented, guarded |
| Parse supplied captions, propose highlight timestamps | Implemented |
| Auto local transcript generation (`faster-whisper`) | Optional; model downloaded on first run |
| Render 1080×1920 H.264 vertical videos, burn SRT, replace soundtrack with own narration | Implemented, FFmpeg required |
| CSV with local downloadable file links | Implemented (`file:///` local, not public internet URLs) |
| YouTube upload as private using OAuth | Implemented: requires own Google client, live test pending |
| Public upload with extra authorization | Implemented: requires `--execute --publish` + `publish_approved=true` |
| Instagram / TikTok API auto-posting | **Not** implemented; manual-ready assets provided |
| Automated viral clips of other creators without rights | Deliberately **not supported** |
| EzzyClips Ultra series (5 formats), titles, hooks, hashtags, credit line | Implemented (`series.py`, `script` command) |
| Originality gate: own narration covers >= 50% of third-party clips | Implemented (`render` refuses otherwise) |
| 2-3 word mobile captions, 20-40 s length check | Implemented |
| Synthetic-media disclosure, English metadata, daily upload cap | Implemented (`synthetic_media`, `DAILY_UPLOAD_CAP`) |
| Channel SEO: description, keywords, series playlists | Implemented (`channel-setup`, dry-run by default) |

## Windows quick start (target `D:\auto_viral_YT`)

1. Clone [the repo](https://github.com/ImranBigDataEngg/viral_yt) into `D:\auto_viral_YT`, or unzip the downloadable project archive there.
2. Install Python 3.11+ and FFmpeg; ensure `python`, `ffmpeg`, `ffprobe` work in PowerShell.
3. Open PowerShell in `D:\auto_viral_YT` and run:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install_windows.ps1
Copy-Item .env.example .env
# Edit .env and add your own YOUTUBE_API_KEY
.\.venv\Scripts\python.exe -m viral_yt.cli discover --top 10
```

Expected: `output\trends\latest_trends.csv` and `.json` with ranked topic-related video URLs. Finding a video does **not** authorize clipping it.

To produce your own clip, copy `examples\assets_template.csv` to `media\authorized\assets.csv`, provide your own actual source, license evidence, local commentary/speech and/or caption track, and deliberately change `approved` to `true` after review:

```powershell
.\.venv\Scripts\python.exe -m viral_yt.cli validate --asset-id my_original_gaming_001
.\.venv\Scripts\python.exe -m viral_yt.cli render --asset-id my_original_gaming_001
.\.venv\Scripts\python.exe -m viral_yt.cli manifest
```

Result: `output\shorts\my_original_gaming_001.mp4`, a JSON sidecar and `output\manifests\download_manifest.csv` with file links. TikTok/Instagram can be uploaded manually from the same MP4.

### Private YouTube upload

In Google Cloud, enable **YouTube Data API v3** and create an **OAuth desktop application**. Put the downloaded OAuth JSON at `.secrets\client_secret.json`. Your own Google browser login may be required (OAuth verification/test-user and app access policies apply). Do not save YouTube passwords in scripts or commit tokens.

```powershell
# Validate only (NO UPLOAD):
.\.venv\Scripts\python.exe -m viral_yt.cli upload --asset-id my_original_gaming_001
# Upload privately after OAuth browser authorization:
.\.venv\Scripts\python.exe -m viral_yt.cli upload --asset-id my_original_gaming_001 --execute
# Public upload (requires publish_approved=true in ledger):
.\.venv\Scripts\python.exe -m viral_yt.cli upload --asset-id my_original_gaming_001 --execute --publish
```

### EzzyClips Ultra channel setup and scripts

```powershell
# Preview the channel description, keywords and playlists (no changes):
.\.venv\Scripts\python.exe -m viral_yt.cli channel-setup
# Apply them (OAuth with the full youtube scope, stored in .secrets\token_manage.json):
.\.venv\Scripts\python.exe -m viral_yt.cli channel-setup --execute --business-email you@example.com
# Print a second-by-second script scaffold for an asset (needs series + subject columns):
.\.venv\Scripts\python.exe -m viral_yt.cli script --asset-id mirror_shot_001
```

See `docs/CHANNEL_KIT.md` for why originality now decides Shorts reach.

**Creation of the YouTube channel itself requires your Google/YouTube session** at https://www.youtube.com/create_channel (or YouTube UI). Choose a handle and check availability. Use the banner/profile design brief under `docs/CHANNEL_KIT.md`.

### Offline demo and tests (no keys or third-party content)

```powershell
.\.venv\Scripts\python.exe .\scripts\demo_synthetic.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

### Optional local ASR (transcripts of footage you are allowed to process)

```powershell
.\.venv\Scripts\pip.exe install -r requirements-optional.txt
.\.venv\Scripts\python.exe -m viral_yt.cli transcribe --asset-id my_original_gaming_001
# Add the generated SRT to transcript_file in assets.csv and render again.
```

## Automation without subscriptions

- Schedule **trend research** daily with Windows Task Scheduler (example `scripts/schedule_discovery.ps1`). No user laptop awake = no local jobs run.
- Keep public publishing approval gated. Do not auto-confirm rights from YouTube public URLs.
- Use a manual editorial batch workflow first, then add official Instagram Graph API / TikTok Content Posting API or supported multi-social scheduling provider when accounts/terms permit.
- GitHub is for source code; **do not commit OAuth tokens, footage, music, results or identifiable creator information without permission**.

## Inexpensive tools

- YouTube Data API v3 — no subscription within assigned API quotas.
- Python + FFmpeg — open source.
- `faster-whisper` — optional local CPU captions (initial model download, compute cost).
- OBS Studio, Audacity, Shotcut and DaVinci Resolve Free — optional original recording/editing tools.
- vidIQ Free — optional keyword/topic research with limited features/credits. Some AI tools and integrations cost credits.
- Metricool Free — optional planner subject to network and plan limits. Check current pricing before relying on it.

## Security

Only put credentials inside `.secrets/` or `.env`; both are gitignored. OAuth access is limited to `youtube.upload`. Generated clips are not stored in GitHub. Google might impose quota, OAuth app verification or account limitations. Back up the local working folder separately.

## Legal and monetization context

YouTube's [reused-content policy](https://support.google.com/youtube/answer/1311392) can exclude minimally altered clips from monetization even when copyright is licensed. The [YouTube Partner Program](https://support.google.com/youtube/answer/72851) has subscriber and viewing thresholds. Other platforms have separate conditions. This software makes no earnings guarantees and cannot certify legal rights automatically.
