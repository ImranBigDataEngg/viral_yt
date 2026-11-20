# VoiceStudio integration (optional voice engine)

[VoiceStudio](https://github.com/debpalash/VoiceStudio) is a local, open-source voice studio with
646 languages, many TTS engines, voice cloning (with consent recording), dubbing and transcription.
TrendTide / EzzyClips can use it as its voice engine for:

| TrendTide feature | Without VoiceStudio | With VoiceStudio |
|---|---|---|
| Voiceover for original Shorts (`composer`) | Piper → flite | VoiceStudio → Piper → flite |
| **Translate / dub** button | Only languages with a Piper voice installed | All 11 dub languages, no per-language download |
| **Make it original** spoken take (TTS) | Piper (English) | VoiceStudio — can be **your own cloned voice** |
| `trendtide doctor` | — | Shows whether the service is up |

If VoiceStudio is down, TrendTide automatically falls back to Piper.

## How it's connected (and why)

TrendTide **never copies, imports or modifies VoiceStudio code**. It talks to an unmodified
VoiceStudio service over its OpenAI-compatible HTTP API (`/v1/audio/speech`,
`/v1/audio/transcriptions`). VoiceStudio is **AGPL-3.0**. Running it as a separate, unmodified
program keeps the copyleft obligations from spreading into the EzzyClips codebase. If you ever
*modify* VoiceStudio and offer it to users over a network, the AGPL requires you to publish your
changes; for closed embedding, its authors sell a commercial licence (voicestudio.sh/pro).
I'm not a lawyer. Get advice before charging customers for anything built on it.

## ⚠️ Model licences (important for a monetized channel / paid app)

The app licence does **not** cover model weights. The **default OmniVoice weights are CC-BY-NC
(non-commercial)**. So are NLLB translation and OuteTTS. For YouTube monetization or the paid
EzzyClips plans, pick a commercially usable engine in VoiceStudio's **Model Manager**. Apache-2.0
or MIT examples listed in its catalog are Kokoro-82M, Fun-CosyVoice3-0.5B, VoxCPM2,
Qwen3-TTS and faster-whisper. Check each model card yourself before use.

## Setup on Windows (desktop app, simplest)

1. Install VoiceStudio from its [Releases page](https://github.com/debpalash/VoiceStudio/releases/latest)
   (Windows x64 installer) and start it once. Its local API listens on `http://127.0.0.1:3900`.
2. In **Model Manager**, install a commercially licensed TTS engine and note its engine id.
3. (Optional) In **Voice cloning**, create a profile of **your own voice** (it records consent)
   and note the profile id/name.
4. Add to `D:\auto_viral_YT\gitcloned\.env`:
   ```
   VOICESTUDIO_URL=http://127.0.0.1:3900
   ```
5. Optional, in `config/settings.json` → `voice.voicestudio`:
   ```json
   "voicestudio": {
     "url": "",
     "model": "kokoro",
     "voice": "my-voice",
     "voices": {"es": "default", "fr": "default"}
   }
   ```
   `model` = engine id (empty = VoiceStudio's active engine), `voice` = default profile,
   `voices` = per-dub-language override.
6. Check: `.\.venv\Scripts\trendtide.exe doctor` → **VoiceStudio (voices/dubbing): OK**.

## Setup as a server (for the hosted web app)

Use VoiceStudio's private-API pattern: a pinned Docker image, bound to loopback or a private
network, protected by an API key. **Never expose port 3900 to the internet.**

```yaml
services:
  voicestudio:
    image: ghcr.io/debpalash/voicestudio:0.5.6   # pin an exact tag
    restart: unless-stopped
    environment:
      OMNIVOICE_API_KEY: ${OMNIVOICE_API_KEY:?set a long random key}
      OMNIVOICE_DATA_DIR: /app/omnivoice_data
    ports: ["127.0.0.1:3900:3900"]
    volumes: [voicestudio-data:/app/omnivoice_data]
volumes: {voicestudio-data: {}}
```

Then in TrendTide's `.env`:
```
VOICESTUDIO_URL=http://127.0.0.1:3900
VOICESTUDIO_API_KEY=<same value as OMNIVOICE_API_KEY>
```
TrendTide refuses to send the key over plain `http://` to anything other than localhost. Use
`https://` (or a private overlay network such as Tailscale) for a remote VoiceStudio host.

## Rules that still apply

* **Clone only voices you own or have written consent for.** Never clone a creator, celebrity or
  game character. That breaks YouTube's policies and VoiceStudio's consent model.
* Clips with a realistic VoiceStudio voice are automatically uploaded with YouTube's
  **altered/synthetic content** disclosure (`containsSyntheticMedia`).
* Dubbing a clip doesn't change who owns it. The rights gate on the source still applies.
