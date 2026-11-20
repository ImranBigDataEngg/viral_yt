# Use EzzyClips on your phone or iPad, and make it fast

EzzyClips runs on your laptop (it does the heavy video work). Phones and tablets open it in the browser, like any
web app. You can upload, review, edit, publish and schedule from the phone.

## Option 1: same Wi-Fi (simplest)

1. In `.env` set `TT_DEV=1`.
2. Start the app with:
   ```powershell
   .\.venv\Scripts\trendtide.exe web --lan
   ```
3. The console prints an address like `http://192.168.1.23:8000`. Open it on the phone or iPad.
4. If Windows asks, **allow Python on Private networks**. If it never asked and the page won't load:
   Windows Security → Firewall → "Allow an app" → tick Python (Private).
5. **Install it like an app:**
   - iPad/iPhone Safari: Share → **Add to Home Screen**.
   - Android Chrome: ⋮ → **Install app**.

The laptop stays at `http://127.0.0.1:8000`; both addresses work at the same time.
Only use `--lan` on a network you trust (home Wi-Fi), not public hotspots.

## Option 2: from anywhere, with https (Tailscale, free for personal use)

1. Install Tailscale on the laptop and on the phone/iPad, and sign in to both with the same account.
2. On the laptop: `tailscale serve --bg 8000`. It prints `https://<laptop-name>.<tailnet>.ts.net`.
3. In `.env`:
   ```
   TT_BASE_URL=https://<laptop-name>.<tailnet>.ts.net
   TT_ALLOWED_HOSTS=<laptop-name>.<tailnet>.ts.net,127.0.0.1,localhost
   TT_EXTRA_ORIGINS=http://127.0.0.1:8000
   TT_DEV=0
   TT_SECRET_KEY=<long random string>
   ```
4. Run `trendtide web` and open the https address on any of your devices. Only devices in your tailnet can reach it.

## Scheduled posts

Scheduled posts are sent by the computer running EzzyClips. Keep it on and awake at the scheduled time: in
Windows power settings, set "When plugged in, put my device to sleep" to **Never**. If it was off, the post goes
out as soon as the app starts again.

## Speed: GPU transcription <a id="speed"></a>

- **Gameplay** mode doesn't transcribe at all, so it takes seconds to minutes.
- For talking videos, an NVIDIA GPU (e.g. a GTX 1650) makes transcription about 4–8× faster:

  ```powershell
  .\.venv\Scripts\pip.exe install nvidia-cublas-cu12 "nvidia-cudnn-cu12==9.*"
  .\.venv\Scripts\trendtide.exe doctor     # shows "Transcription speed: GPU (NVIDIA CUDA)"
  ```

  On 4 GB cards the `small` model with `int8_float16` fits comfortably. If anything goes wrong on the GPU, the app
  falls back to the CPU on its own. To force one or the other, set `TT_ASR_DEVICE=cpu|cuda`, or
  `"asr": {"device": ...}` in `config/settings.json`.
- YouTube links that have captions skip transcription completely.
