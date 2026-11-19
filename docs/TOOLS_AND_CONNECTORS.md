# Free, legit tools, plugins & connectors for TrendTide

Rule of thumb: prefer **official APIs** and **local open-source** tools; avoid anything that scrapes, rips
videos, fakes engagement, or needs your password.

## Already wired into this repo
| Tool | Cost | Used for |
|---|---|---|
| YouTube Data API v3 | Free quota | Trend radar, uploads |
| FFmpeg (Gyan build on Windows) | Free | All rendering, loudness, captions |
| Piper TTS (`piper-tts`, MIT) | Free, offline | Natural voice-over (fallback: FFmpeg flite) |
| faster-whisper | Free, offline | Word-timed captions; transcripts of *authorized* media |
| Ollama + Llama 3.1 8B (optional) | Free, offline | Draft briefs (`llm.engine = "ollama"`) — still fact-check |
| Google Drive API (`drive.file`) | Free 15 GB | Phone-friendly download links |
| GitHub Actions | Free for public repos / free minutes on private | Tests on every push |

## Creation tools (free tiers)
- **OBS Studio** — record your gameplay/screen/face-cam (the best Lane B source: footage you own).
- **DaVinci Resolve (free)** / **CapCut desktop** — manual polish when a Short deserves it.
- **Audacity** — clean your voice recordings (noise reduction, normalize).
- **YouTube Audio Library** — music/SFX cleared for YouTube (check attribution column). On TikTok/IG add music
  in-app from their licensed libraries instead of baking it into the MP4.
- **Canva free** / **Figma** — logo, banner, thumbnails from the brand kit.
- **Pexels / Pixabay** — free stock b-roll (record the licence page URL in `visual_rights`).
- **NASA Image & Video Library** — mostly public-domain space footage for the Science & Space category.

## Research
- **Google Trends** (free) — confirm a topic is rising outside YouTube.
- **vidIQ / TubeBuddy free tiers** — keyword ideas; ignore "AI title" upsells.
- **YouTube Studio → Inspiration / Research tab** — what your audience searches.

## Connectors you can enable in Claude (claude.ai → Settings → Connectors)
| Connector / skill | Why it helps |
|---|---|
| **GitHub** (+ install the Claude GitHub App on `ImranBigDataEngg/viral_yt`) | Lets Claude push commits/PRs directly (currently read-only for this session) |
| **Windsor.ai → YouTube** (already available as a skill) | Ask "which Shorts kept viewers past 3 s this week?" — channel/Shorts analytics without exporting CSVs |
| **Windsor.ai → TikTok organic / Instagram** | Same review loop for cross-posts; Instagram connector can also publish Reels after you approve each draft |
| **Google Drive** | Read/write your export folder from chat |
| **youtube-transcriber skill** | Read captions of a video *for research notes* (never re-use the footage or text verbatim) |
| **Claude desktop app + "Add folder" (D:\auto_viral_YT)** | Lets Claude read/write your local project directly next time |

## Avoid (not legit or will hurt the channel)
yt-dlp/“video downloader” workflows on other people's videos, re-upload/“reupload bot” services, view/sub
sellers, engagement pods, unofficial TikTok/IG auto-posters that ask for your password, AI “news” with invented facts.
