# EzzyClips vs ClipIt Studio (clipitstudio.app), reviewed 20 Nov 2026

Source: ClipIt's public landing page. The app itself (`/app/`) needs a Google sign-in, so the editor
wasn't inspected directly. Nothing was copied: no code, text, design or assets.

## What ClipIt does well

1. **Gameplay-first detection.** Loudness, sudden sounds, fast motion and scene cuts give ranked moments
   (scores like 96) in seconds. No transcription is needed.
2. **Sliders** to tune what counts as a highlight.
3. **Fixed lengths:** 30 / 45 / 60 s.
4. **Streamer layouts:** blur fill, crop, face tracking, **facecam split** (webcam top, gameplay bottom), fit.
5. **Editor:** trim, split, lengthen, **speed, volume, fades, undo**.
6. **Hook-text templates, progress bar,** and on-device word-by-word captions.
7. **Low friction:** drop a file, go. 1080×1920 export plus square and 16:9.

## Gap analysis and what was built

| Area | Before | Now (this change) |
|---|---|---|
| Gameplay detection | Speech first. Loudness-only fallback. Always ran Whisper (minutes). | **`action.py`:** loudness + sudden sounds + motion (5 fps luma diff) + scene cuts, ranked 0–99. **No transcription.** Audio and video passes run in parallel. Window = 65% build-up / 35% payoff, snapped to a scene cut. |
| Video type | — | **Auto / 🎮 Gameplay / 🎙 Talking** toggle. |
| Tuning | — | **Advanced sliders:** Loudness, Sudden sounds, Fast motion, Scene cuts (0–3). |
| Length | Min/max config only | **Auto / 30 / 45 / 60 s.** |
| Layouts | Smart (speaker), blur fill | + **Facecam split** (auto-finds a static webcam face near the edge, or ↖↗↙↘), **Crop**, **Fit**. Switchable in the editor. |
| Clip score card | Hook / Flow / Value | Gameplay clips show **Loud / Sudden / Motion**. |
| Hook text | Auto from transcript | + **10 templates** in the editor. Gameplay default: "Wait for it…" / "No way!". |
| Progress bar | — | Optional bar, on the dashboard and in the editor. |
| Editor | Trim, captions, style, cold open | + **Speed 0.5–2×, volume ±12 dB, fade in/out, layout switch, progress bar, Undo/Redo (Ctrl+Z / Ctrl+Y).** Speed and fades preview live. |
| Friction | Rights basis + 8-char evidence for every upload | **"I created it" preselected. Evidence optional for your own recordings**: the signed attestation (account + time) is stored as evidence. |

## Where EzzyClips is already ahead

- Speech-aware highlights with an LLM rerank, and cold opens on the best line.
- 4 caption styles with real word timing.
- **Multi-language dubbing** and VoiceStudio voices.
- **Make it original** (your spoken take) and the originality score.
- **Auto-post** to YouTube / Instagram / TikTok.
- **Rights-checked YouTube links.** ClipIt's website no longer accepts YouTube links at all.
- Subscriptions and billing, multi-user accounts, IDOR/CSRF protections.

## Still not done (candidates for next)

- **Split a clip in two** in the editor, and a **waveform / filmstrip** on the timeline.
- **Live clipping** from Twitch/YouTube streams, and a desktop recorder with a "mark moment" hotkey.
- **Background music** (up to 2 tracks, licensed library only).
- **In-browser processing.** ClipIt runs on-device in the browser; ours runs on your PC through the local
  server, which is already private when self-hosted.
