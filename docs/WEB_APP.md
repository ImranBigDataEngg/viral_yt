# EzzyClips Ultra (TrendTide web app): setup, payments, security, deployment

The web product is branded **EzzyClips Ultra** (`TT_BRAND_NAME`); the CLI and the YouTube channel stay TrendTide.

`trendtide web` serves a full SaaS: accounts, Stripe subscriptions ($2/day, $25/month, $250/year), the **manual clipper**
(paste a link or upload → N clips with hooks, captions, active-speaker framing, 9:16 / 1:1 / 16:9), and the
**browser review editor** (trim handles, caption text/timing edits, hook, style, cold open → final render).

## Run locally (5 minutes)
```powershell
py scripts\install.py --ai --web                          # web deps + OpenCV + verified face model
copy .env.example .env                                   # TT_DEV=1 is fine locally
.\.venv\Scripts\trendtide.exe web                        # http://127.0.0.1:8000
```
Without Stripe keys you can still sign up and browse. To test payments use **test mode**:
1. Stripe Dashboard (test mode) → Developers → API keys → put `sk_test_…` in `STRIPE_SECRET_KEY`.
2. `trendtide stripe-setup` → copies the three `price_…` IDs; paste into `.env`.
3. Install the Stripe CLI, then `stripe listen --forward-to 127.0.0.1:8000/stripe/webhook` → paste the `whsec_…` it prints.
4. Subscribe with card `4242 4242 4242 4242`, any future date and CVC. Your plan turns active when the webhook arrives.
5. Settings → Billing → Customer portal: enable "cancel subscription" and "switch plans" (Stripe Dashboard).

## Channel verification (for YouTube links)
Google Cloud → Credentials → OAuth client **Web application**, redirect URI `https://YOUR_DOMAIN/connect/youtube/callback`
(and `http://127.0.0.1:8000/connect/youtube/callback` for dev) → save JSON as `.secrets/google_web_client.json`.
Scope requested: `youtube.readonly` only. We store the channel ID and discard Google's token immediately.
Apps requesting YouTube scopes need Google's OAuth verification before going public (free, takes time).

## What a link job does
| Input | Rights decision | Then |
|---|---|---|
| YouTube video / finished live on a **verified** channel | `original` | yt-dlp download (no cookies), YouTube's word-timed captions reused (skips ASR) |
| YouTube video with **Creative Commons BY** licence | `cc_by` (credit burned in + described) | same |
| Someone else's standard-licence YouTube video | **rejected before download** | message explains the options |
| Live stream still running | job waits, re-checks every 10 min | starts automatically when the VOD exists |
| Direct https file link / upload | user-declared basis + evidence + attestation | SSRF-safe download / validated upload |

## Security model (what's implemented)
| Threat | Control |
|---|---|
| Password theft / cracking | scrypt (N=2¹⁴, r=8, p=1, 16-byte salt), constant-time compare, 10-char minimum + common-password block |
| Credential stuffing / brute force | persistent throttle: 5 failures / 15 min per IP **and** per email; identical error for unknown emails; dummy hash equalises timing |
| Session hijack | 256-bit random tokens, only SHA-256 stored server-side, HttpOnly + Secure + SameSite=Lax cookies, rotation on login, all sessions revoked on password change |
| CSRF | synchronizer token on every form + `X-CSRF-Token` on every JSON POST + Origin check; signed double-submit token before login |
| XSS | Jinja autoescape, no inline JS/CSS, strict CSP (`script-src 'self'`, `object-src 'none'`, `frame-ancestors 'none'`), editor escapes every caption |
| Clickjacking / sniffing | `X-Frame-Options: DENY`, `nosniff`, COOP/CORP, Referrer-Policy, Permissions-Policy, HSTS in production |
| IDOR | every job/clip/media query filtered by `user_id`; file paths verified inside the user's folder; unguessable IDs |
| SSRF | https only, no credentials, port 443 only, DNS resolved and private/loopback/link-local/reserved IPs rejected, **every redirect re-validated** |
| Malicious uploads | extension allow-list, size cap while streaming, ffprobe must find a video stream, random server-side names, never served from a public path |
| Host-header attacks | TrustedHostMiddleware + explicit `TT_BASE_URL` for redirects |
| Payment fraud / spoofing | Stripe-hosted Checkout & Portal (card data never touches us: PCI SAQ A); entitlements change **only** on webhooks with valid HMAC-SHA256 signature, 5-minute replay window, event-ID idempotency |
| Abuse / cost blow-ups | per-plan daily video limits, max source length, preview rate limit, sign-up rate limit, single active re-render per clip |
| Secrets | env-only; production refuses to start without `TT_SECRET_KEY`, https base URL and allowed hosts |
| SQL injection | parameterised queries only |
| Command injection | FFmpeg/yt-dlp called with argument lists, never a shell |

Covered by `tests/test_web.py` (real HTTP): CSRF and Origin, login throttle with no user enumeration, webhook
signature/replay/idempotency, entitlement gating, SSRF list, IDOR across users, full upload → clips → preview → edited render.

**Before taking real money:** run `pip-audit`, an OWASP ZAP baseline scan against staging, and Stripe's test-mode
checklist. Get the Terms/Privacy templates reviewed by a lawyer and register for sales tax (GST/HST in Canada; Stripe Tax
can calculate it). No independent penetration test has been done.

## Production deployment (single server)
```
Internet → Caddy (auto-HTTPS, HTTP/2) → uvicorn 127.0.0.1:8000 (trendtide web) + trendtide worker (separate process)
```
- `TT_DEV=0`, `TT_BASE_URL=https://app.example.com`, `TT_ALLOWED_HOSTS=app.example.com`, `TT_WORKERS=0` on the web
  process. Run `trendtide worker` as its own service (systemd / NSSM on Windows).
- Caddyfile: `app.example.com { reverse_proxy 127.0.0.1:8000 \n request_body { max_size 4GB } }`
- Back up `data/web.db` daily (SQLite WAL is fine for hundreds of users; move to Postgres + object storage to scale).
- GPU box optional. NVENC/QSV/AMF are auto-detected.

## Clip actions (job page)
- **Select / Select all → Download selected** zips the chosen MP4s. Every clip also has its own Download button.
- **Make original** adds a short spoken intro with *your* take (suggested by the AI, edited by you; TTS voice) on a
  blurred freeze-frame card, then the clip. It's real commentary, so the originality score rises; a disguise
  (mirroring, speed-ups, filters) would not, and doesn't fool Content ID or YouTube's reused-content review.
- **Translate & dub** (es, fr, de, pt, it, hi, ar, ru, tr, zh): translates the speech (your LLM, or offline Argos
  Translate), re-voices it with a Piper voice, fits each line to its slot, keeps the original audio as a quiet bed and
  burns translated captions. Output is a new clip `…_es`, marked as synthetic media when posted.
  Install voices: `py scripts\install.py --voices es,hi,fr`.

## Social posting setup
Posting only works for accounts connected through each platform's own login. A typed handle can't authorise uploads.
| Platform | What you need | Restrictions until approved |
|---|---|---|
| YouTube | Google OAuth web client (same as channel verification) with the redirect `…/social/callback/youtube`, YouTube Data API enabled | Apps that haven't passed Google's API audit have uploads locked **private** |
| Instagram | Meta developer app → "Instagram API with Instagram Login", redirect `…/social/callback/instagram`, permissions `instagram_business_basic` + `instagram_business_content_publish`; the user's account must be Business/Creator | Public use needs Meta App Review |
| TikTok | TikTok developer app with Login Kit + Content Posting API (Direct Post on), redirect `…/social/callback/tiktok`, scopes `user.info.basic,video.publish` | Unaudited apps: posts are **private only**, max 5 users per 24 h |

Users pick clips, tick destinations, choose visibility (TikTok "who can view" must be chosen explicitly), confirm,
and press Publish. Public posts require originality ≥ 70; the per-platform limit is 10 posts per day by default.

## Known gaps (next steps)
- Email verification and password reset (needs an email provider: Postmark, SES or Resend).
- 2FA (TOTP) for accounts.
- Admin dashboard and refunds UI (use the Stripe Dashboard meanwhile).
- Direct posting to YouTube/TikTok/Instagram from the web app (the CLI uploader exists; per-user OAuth is next).
- Card fees eat a big share of a $2 charge (≈2.9% + $0.30 per payment ≈ 18%). Consider $5/3-day passes.
