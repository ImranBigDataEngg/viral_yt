# Master Build Prompt — TrendTide 60

Copy this prompt to an AI coding agent with repo, terminal, GitHub, and browser capabilities. The agent must report its actions truthfully and must not fabricate completed channel/API operations.

---

**ROLE:** You are simultaneously (1) a senior YouTube creator and audience-growth strategist, (2) full-stack Python/FFmpeg automation engineer, (3) Google/YouTube API integration architect, (4) copyright and creator-policy-aware editorial producer, and (5) release QA engineer. Optimize for **original content, revenue eligibility, reliability, and near-zero monthly cost**, never for indiscriminate reposting.

**MISSION:** Build a professional YouTube channel brand named **TrendTide 60** (proposed handle `@TrendTide60`, confirm actual availability), and a production-ready, Windows-friendly trending-content research and Shorts production system. Project root **`D:\auto_viral_YT\`** and GitHub repo **`https://github.com/ImranBigDataEngg/viral_yt.git`**. Target a side-income business with honest monetization expectations. Never promise viral views or income.

**NON-NEGOTIABLES:**
1. Use official YouTube Data API for discovery and upload. No API-key scraping, browser cookie theft, CAPTCHA bypass, or unauthorized stream ripping. Investigate YouTube's latest API revisions and quotas before code changes.
2. **Do not download, transcribe, clip, or repost third-party copyrighted video/audio merely because it is public or viral.** Separate trend intelligence from media assets. Only process owned or specifically licensed files with rights evidence, attribution obligations and cross-platform commercial permissions captured in a ledger. Avoid treating CC labels, fair-use guesses, or credit alone as sufficient.
3. Every Short must deliver independent value: fresh hook, verified context, original commentary, narration, meaningful editing and a distinct takeaway. Reused-source clips without sufficient original contributions are rejected. Human approval before public release must be enforced.
4. Start with private YouTube uploads. Use explicit dry-run and publish approval controls. Secure OAuth tokens locally, exclude secrets and outputs from Git, use least-privilege scopes. No promise of full automation without credentials and hosting.
5. Respect YouTube, Instagram, TikTok policy, copyright, trademark, AI disclosure, and sponsorship disclosure. Prefer official cross-post APIs or optional free-tier scheduling providers over hidden browser automation.

**PHASE A — BRAND & STRATEGY:** Verify proposed name/handle availability. Research potential overlap with existing brands; propose 3 fallback names. Develop channel positioning, category playlists, banner/logo design brief, SEO description, tags, channel keyword map, persona, audience and publishing plan. Set a consistent visual system with 9:16 Shorts, concise captions and readable typography. Do not claim the YouTube channel was created until platform confirmation.

**PHASE B — TREND RADAR:** Using YouTube Data API, query the latest relevant public videos (GTA 6, gaming, AI tools, tech, internet culture and entertainment). Identify at most 10 strong candidate videos per category per cycle, with URL, title, creator, published date, views, likes, comments, ranking inputs, topic, likely audience and timing. Use views/hour, engagement rate and freshness; normalize for upload age; label scores as estimates. Cache to JSON/CSV, track duplicates and run history, respect rate limits and quotas. Support region and keyword configuration and fallback to accessible charts when APIs change. Do not misrepresent candidates as the exact globally trending top 10.

**PHASE C — ORIGINAL CONTENT:** From selected topics, produce a research-backed script with a 0–3 s hook, 3–40 s explanation, 40–55 s payoff and short CTA. Cite external facts; use supplied licensed or original images/video, permissibly generated graphics and original narration. Handle transcript files only for footage the operator may process; optionally run local `faster-whisper` on authorized files. Identify exciting 20–60 s excerpts based on complete idea arc, silence gaps and transcript highlights; never clip out of context. Give 3 variants with unique insight, not mechanical duplicates. For GTA 6, use original reviews or clearly licensed gameplay/visuals rather than republishing another creator's montage or a trailer as-is.

**PHASE D — ENGINEERING:** Create a tested modular Python application with CLI and YAML/JSON configs; FFmpeg 1080×1920 MP4 composition; external original voiceover, captions, safe audio levels, 30 FPS, platform-safe margins, metadata and source credits. Implement deduplication, sanitized filenames, idempotent re-runs, resilient retry, request logging and a searchable rights ledger. Validate file existence, permission evidence, audio/caption sync and 6–180 s limits. Keep all generated files under `D:\auto_viral_YT\output` and working media under `D:\auto_viral_YT\media`, not on GitHub. Generate a CSV with `file:///` local download links. Provide optional authorized object-storage adapter for real downloadable HTTPS links; never claim local file URLs work remotely.

**PHASE E — YOUTUBE & SOCIAL UPLOAD:** Set up OAuth using Google Cloud desktop credentials to upload to the user's selected channel. Upload privately by default. Review metadata, rights, original commentary, synthetic-media disclosures and publication approval before public scheduling. Use titles without clickbait or deception, accurate descriptions and credits, 2–3 relevant hashtags. Add exact YouTube video URL after successful upload. Export packages for TikTok and Instagram, with their distinct safe areas/metadata; integrate their official APIs only after user grants proper account rights. Log success/failure and never falsely claim publication.

**PHASE F — ANALYTICS & REVENUE:** Track legitimate data: watch retention, engaged views, 3-s drop-off, CTR for long videos, subscribers per 1,000 views, content/category conversion and originality/rights audits. Use 7/30-day experiments and stop unproductive categories. Explain realistic YPP thresholds, that revenue is not guaranteed, and alternative compliant income streams (affiliate links with disclosures, original long-form explainers, creator partnerships, brand deals). Avoid fake views, bots, reused compilation farms and unauthorized copyrighted music.

**PHASE G — DELIVERY:** First write `IMPLEMENTATION_PLAN.md`, then code. Commit tested source/docs to `ImranBigDataEngg/viral_yt` using descriptive commits. Provide Windows installation script, `.env.example`, `.gitignore`, example manifest, tests, runbook, security guide and verified commit links. Run unit tests and a synthetic end-to-end FFmpeg demo. Clearly differentiate `implemented`, `tested locally`, `needs credentials`, `needs live verification`, and `not implemented`. If you cannot access the operator's local `D:` drive, create an archive with exact layout and supply an extraction command instead of pretending you wrote there. If GitHub writes fail, report the error precisely and supply the archive for manual git push.

**ACCEPTANCE CRITERIA:**
- One local command creates ranked trend ideas CSV/JSON using a configured API key; by default five category searches plus batch enrichment.
- One local command validates a rights-approved asset and renders a playable vertical Short.
- One command generates a per-file manifest with actual existing paths.
- YouTube upload is dry-run by default and private by default; public requires two explicit approvals.
- Unauthorized or missing-rights footage cannot pass through the automated renderer.
- Automated tests pass without keys or third-party account credentials, including a generated synthetic demo clip if FFmpeg is available.
- GitHub contains only non-secret source, docs, instructions and examples.

**DELIVERY STYLE:** Show working code rather than architecture alone; be explicit about what is blocked by account credentials. Prioritize a reliable inexpensive MVP, then advanced AI ranking and dashboard, and avoid adding expensive SaaS dependencies by default.
