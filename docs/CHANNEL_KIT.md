# EzzyClips Ultra — Channel setup kit

- **Channel:** EzzyClips Ultra — `@EzzyClipsUltra` — https://www.youtube.com/channel/UCLK8oDnX_41HK2VBdQrYl9Q
- **Tagline (banner):** The story behind the clip.
- **Promise:** viral clips explained in under 60 seconds — the trick, the hidden detail, the origin, the verdict.
- **Voice:** sharp, curious, conversational; every Short adds a breakdown the original clip does not have.
- **Audience:** English-speaking Shorts viewers, starting in Canada and global English markets.

> Earlier drafts of this kit used the working name "TrendTide 60". The live channel is EzzyClips Ultra; the Python package keeps its `trendtide60` name.

## Why originality is the growth strategy

Since **October 1, 2026**, YouTube's Shorts recommendations favour original content and give less reach to channels that mostly re-upload others' clips with few changes. Non-original Shorts also earn nothing from the Shorts revenue pool, and the reused-content policy can remove monetization channel-wide. So this pipeline enforces:

- Your narration/graphics cover **at least 50%** of any Short built on third-party footage (`rights.check_originality`; house rule, not a YouTube number).
- Every asset belongs to a **series** that answers a question the source clip does not.
- The source creator is **credited** in the description.
- At most **2 uploads per day** (`DAILY_UPLOAD_CAP`), with human approval (`publish_approved`) before anything goes public.
- Realistic AI visuals/voices set `synthetic_media=true`, which sends YouTube's altered-or-synthetic disclosure.

## About section and keywords

Applied by `python -m viral_yt.cli channel-setup --execute` (dry-run without `--execute`). The text lives in `src/viral_yt/channel.py`:

- Description front-loads the search phrases in the first 150 characters.
- Keywords: viral clips explained, how was this filmed, meme origins, video editing tricks, camera tricks, optical illusions explained, viral moments breakdown, internet trends explained, real or fake, behind the clip.
- Country CA, language English, and one public playlist per series.

## Five series (`src/viral_yt/series.py`)

| Key | Series | Title formula |
| --- | --- | --- |
| `how_filmed` | How Was This Filmed? | How Was This {subject} Filmed? |
| `meme_origins` | Meme Origins | The Real Origin of the {subject} Meme |
| `missed_this` | You Missed This | You Missed This in the Viral {subject} Clip |
| `real_or_fake` | Real or Fake? | Is the Viral {subject} Video Real? |
| `trend_30s` | Trend in 30s | {subject} Explained in 30 Seconds |

`python -m viral_yt.cli script --asset-id <id>` prints the hook, beats (0–1.5 s hook, stakes, breakdown, payoff + loop), comment prompt and hashtags for an asset.

## Short spec

1080×1920, 20–40 s, burned-in captions in 2–3 word chunks placed above the bottom ~20%, own voiceover, hook in the first 1.5 s, last frame cuts back to the first so it loops. 3–5 specific hashtags (topic first).

## Manual checklist (Studio settings the API cannot set)

- [ ] Settings > Channel > Advanced: audience "No, not made for kids".
- [ ] Settings > Upload defaults: category, language English, description template with the credit line.
- [ ] Customization > Profile > Links: TikTok, Instagram, Ezzyclips.ultra site once live.
- [ ] Allow remixing of your Shorts.
- [ ] After 10 Shorts, set the best performer as the featured video.
- [ ] Judge each series after 10 Shorts by "viewed vs swiped away" and average % viewed; drop the weakest.
