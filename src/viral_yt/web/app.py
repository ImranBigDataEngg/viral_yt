"""TrendTide web app. Run: trendtide web  (or: uvicorn viral_yt.web.app:create_app --factory)

Security model (see docs/WEB_SECURITY.md): hashed server-side sessions in HttpOnly/SameSite cookies, CSRF tokens on
every state change + Origin check, scrypt passwords, persistent login throttling, strict CSP (no inline JS),
per-user authorisation on every object, SSRF-safe ingest, upload size/type validation, Stripe-hosted payments with
signed webhooks as the only source of entitlements.
"""
from __future__ import annotations

import json
import mimetypes
import secrets
import shutil
import time
import urllib.parse
from pathlib import Path

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.middleware import Middleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from ..captions import STYLES, group_words
from ..common import env
from ..ffmpeg_tools import RATIOS
from ..rights import APPROVED_LICENSES
from . import billing, security
from .config import PLANS, WebConfig, load
from .db import DB, now
from .worker import enqueue, job_dir, start_threads, user_dir

HERE = Path(__file__).parent
import jinja2  # noqa: E402

templates = Jinja2Templates(env=jinja2.Environment(loader=jinja2.FileSystemLoader(str(HERE / "templates")),
                                                   autoescape=True))
BRAND = env("TT_BRAND_NAME", "EzzyClips Ultra")
BASIS_LABELS = {"original": "I created it / it's my channel", "permission": "Written permission from the creator",
                "licensed": "Licence or clipping-programme terms", "cc_by": "Creative Commons BY",
                "public_domain": "Public domain"}


class SecurityHeaders:
    def __init__(self, app, secure: bool):
        self.app, self.headers = app, security.security_headers(secure)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def _send(message):
            if message["type"] == "http.response.start":
                message.setdefault("headers", [])
                message["headers"] = list(message["headers"]) + self.headers
                if scope["path"].startswith(("/app", "/api", "/media", "/billing", "/account")):
                    message["headers"].append((b"cache-control", b"no-store"))
            await send(message)
        await self.app(scope, receive, _send)


mimetypes.add_type("application/manifest+json", ".webmanifest")   # PWA manifest (Windows lacks this mapping)


def create_app(cfg: WebConfig | None = None, start_workers: bool = True) -> Starlette:
    cfg = cfg or load()
    db = DB(cfg.data_dir / "web.db")
    origin = "{0.scheme}://{0.netloc}".format(urllib.parse.urlsplit(cfg.base_url))
    # extra trusted origins (e.g. --lan: the laptop's own 127.0.0.1 address next to the Wi-Fi address)
    origins = {None, origin} | {o.strip().rstrip("/") for o in env("TT_EXTRA_ORIGINS", "").split(",") if o.strip()}

    # ---------------------------------------------------------------- helpers
    def client_ip(req: Request) -> str:
        return req.client.host if req.client else "unknown"

    def current(req: Request) -> tuple[dict | None, dict | None]:
        s = security.get_session(db, req.cookies.get(security.SESSION_COOKIE))
        u = db.one("SELECT * FROM users WHERE id=?", (s["user_id"],)) if s else None
        return (s, u) if u else (None, None)

    def require_user(req: Request) -> tuple[dict, dict]:
        s, u = current(req)
        if not u:
            raise HTTPException(303, headers={"Location": "/login?next=" + urllib.parse.quote(req.url.path)})
        return s, u

    def page(req: Request, name: str, ctx: dict | None = None, status: int = 200):
        s, u = current(req)
        ent = billing.entitlement(db, u["id"]) if u else None
        base = {"user": u, "csrf": s["csrf"] if s else "", "ent": ent, "plans": PLANS, "dev": cfg.dev,
                "brand": BRAND, "path": req.url.path}
        return templates.TemplateResponse(req, name, {**base, **(ctx or {})}, status_code=status)

    async def form_checked(req: Request, session: dict) -> dict:
        if req.headers.get("origin") not in origins:
            raise HTTPException(403, "Bad origin")
        form = await req.form(max_files=0, max_fields=40)
        if not security.csrf_ok(session, form.get("csrf")):
            raise HTTPException(403, "Invalid or expired form. Reload the page and try again.")
        return form

    def api_checked(req: Request, session: dict) -> None:
        if req.headers.get("origin") not in origins or not security.csrf_ok(session, req.headers.get("x-csrf-token")):
            raise HTTPException(403, "CSRF check failed")

    def set_session_cookie(resp: Response, token: str) -> None:
        resp.set_cookie(security.SESSION_COOKIE, token, max_age=security.SESSION_TTL, httponly=True,
                        secure=cfg.secure_cookies, samesite="lax", path="/")

    def safe_next(n: str | None) -> str:
        return n if n and n.startswith("/") and not n.startswith("//") and "\\" not in n else "/app"

    def owned_output(u: dict, oid: str) -> dict:
        out = db.one("SELECT * FROM outputs WHERE id=? AND user_id=?", (oid, u["id"]))
        if not out:
            raise HTTPException(404)
        root = user_dir(cfg.data_dir, u["id"]).resolve()
        if not Path(out["file"]).resolve().is_relative_to(root):
            raise HTTPException(404)
        return out

    # ---------------------------------------------------------------- public pages
    def testimonials() -> list[dict]:
        """Only REAL quotes the owner has permission to publish: config/testimonials.json entries with
        "permission": true. Nothing is shown (the section is hidden) until real ones exist."""
        from ..common import ROOT
        f = ROOT / "config" / "testimonials.json"
        try:
            items = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else []
        except ValueError:
            items = []
        return [t for t in items if isinstance(t, dict) and t.get("permission") is True and t.get("quote") and t.get("name")][:12]

    async def landing(req: Request):
        return page(req, "landing.html", {"testimonials": testimonials()})

    async def legal(req: Request):
        doc = req.path_params["doc"]
        if doc not in ("terms", "privacy", "acceptable-use"):
            raise HTTPException(404)
        return page(req, "legal.html", {"doc": doc})

    async def start(req: Request):
        """Landing-page link box: logged in -> dashboard prefilled; otherwise sign up first, then continue."""
        url = (req.query_params.get("url") or "").strip()[:2048]
        target = "/app" + ("?url=" + urllib.parse.quote(url, safe="") if url else "")
        s, u = current(req)
        if u:
            return RedirectResponse(target, 303)
        return RedirectResponse("/signup?next=" + urllib.parse.quote(target, safe=""), 303)

    async def healthz(req: Request):
        return JSONResponse({"ok": True})

    async def favicon(req: Request):
        return FileResponse(HERE / "static" / "favicon.svg", media_type="image/svg+xml")

    # ---------------------------------------------------------------- auth
    async def signup(req: Request):
        if req.method == "GET":
            return page(req, "auth.html", {"mode": "signup", "csrf": _pre_csrf(req), "next": req.query_params.get("next", "")})
        form = await req.form(max_files=0, max_fields=10)
        if not _pre_csrf_ok(req, form.get("csrf")):
            raise HTTPException(403, "Invalid form. Reload and try again.")
        if security.rate_limited("signup:" + client_ip(req), int(env("TT_SIGNUPS_PER_IP_HOUR", "5")), 3600):
            return page(req, "auth.html", {"mode": "signup", "error": "Too many sign-ups from your network. Try later.",
                                           "csrf": _pre_csrf(req)}, 429)
        email = security.normalize_email(form.get("email", ""))
        pw = form.get("password", "")
        err = None if email else "Enter a valid email."
        err = err or security.password_problem(pw, email or "")
        err = err or (None if form.get("terms") == "on" else "Please accept the Terms and Acceptable Use Policy.")
        if not err and db.one("SELECT id FROM users WHERE email=?", (email,)):
            err = "Couldn't create that account. If you already have one, log in instead."
        if err:
            return page(req, "auth.html", {"mode": "signup", "error": err, "email": email or "", "csrf": _pre_csrf(req),
                                           "next": form.get("next", "")}, 400)
        uid = secrets.token_hex(12)
        db.run("INSERT INTO users (id,email,pw_hash,created,accepted_terms) VALUES (?,?,?,?,?)",
               (uid, email, security.hash_password(pw), now(), now()))
        nxt = safe_next(form.get("next"))
        resp = RedirectResponse("/billing?welcome=1&next=" + urllib.parse.quote(nxt), 303)
        set_session_cookie(resp, security.create_session(db, uid, client_ip(req), req.headers.get("user-agent", "")))
        return resp

    async def login(req: Request):
        if req.method == "GET":
            return page(req, "auth.html", {"mode": "login", "next": req.query_params.get("next", ""), "csrf": _pre_csrf(req)})
        form = await req.form(max_files=0, max_fields=10)
        if not _pre_csrf_ok(req, form.get("csrf")):
            raise HTTPException(403, "Invalid form. Reload and try again.")
        email = security.normalize_email(form.get("email", "")) or ""
        keys = ("ip:" + client_ip(req), "email:" + email)
        ctx = {"mode": "login", "email": email, "next": form.get("next", ""), "csrf": _pre_csrf(req)}
        if security.throttled(db, *keys):
            return page(req, "auth.html", {**ctx, "error": "Too many attempts. Wait 15 minutes and try again."}, 429)
        user = db.one("SELECT * FROM users WHERE email=?", (email,)) if email else None
        ok = security.verify_password(form.get("password", ""), user["pw_hash"] if user else security.DUMMY_HASH)
        if not (user and ok):
            security.record_failure(db, *keys)
            return page(req, "auth.html", {**ctx, "error": "Email or password is incorrect."}, 401)
        security.clear_failures(db, *keys)
        security.destroy_session(db, req.cookies.get(security.SESSION_COOKIE))      # rotate
        resp = RedirectResponse(safe_next(form.get("next")), 303)
        set_session_cookie(resp, security.create_session(db, user["id"], client_ip(req), req.headers.get("user-agent", "")))
        return resp

    async def logout(req: Request):
        s, _ = require_user(req)
        await form_checked(req, s)
        security.destroy_session(db, req.cookies.get(security.SESSION_COOKIE))
        resp = RedirectResponse("/", 303)
        resp.delete_cookie(security.SESSION_COOKIE, path="/")
        return resp

    # Pre-login CSRF: double-submit cookie signed with the secret key
    def _pre_csrf(req: Request) -> str:
        import hashlib
        import hmac
        seed = req.cookies.get("tt_pre") or ""
        return hmac.new(cfg.secret_key.encode(), seed.encode(), hashlib.sha256).hexdigest() if seed else ""

    def _pre_csrf_ok(req: Request, supplied: str | None) -> bool:
        import hmac
        exp = _pre_csrf(req)
        return bool(exp and supplied and hmac.compare_digest(exp, supplied))

    # ---------------------------------------------------------------- app pages
    async def dashboard(req: Request):
        s, u = require_user(req)
        jobs = db.all("SELECT id,kind,status,progress,error,created,spec FROM jobs WHERE user_id=? AND kind='link' "
                      "ORDER BY created DESC LIMIT 50", (u["id"],))
        for j in jobs:
            sp = json.loads(j.pop("spec"))
            j["label"] = sp.get("url") or sp.get("upload_name") or "upload"
            j["outputs"] = db.one("SELECT COUNT(*) AS n FROM outputs WHERE job_id=? AND user_id=?", (j["id"], u["id"]))["n"]
            first = db.one("SELECT id FROM outputs WHERE job_id=? AND user_id=? ORDER BY id LIMIT 1", (j["id"], u["id"]))
            j["poster"] = f"/media/{first['id']}/poster" if first else ""
        channels = db.all("SELECT channel_id,title FROM channels WHERE user_id=?", (u["id"],))
        return page(req, "dashboard.html", {"jobs": jobs, "channels": channels, "ratios": list(RATIOS),
                                             "styles": list(STYLES), "bases": BASIS_LABELS,
                                             "notice": req.query_params.get("notice", ""),
                                             "prefill": (req.query_params.get("url") or "")[:2048]})

    async def create_job(req: Request):
        s, u = require_user(req)
        if req.headers.get("origin") not in origins:
            raise HTTPException(403, "Bad origin")
        ent = billing.entitlement(db, u["id"])
        if not ent:
            return RedirectResponse("/billing?need=1", 303)
        used = db.one("SELECT COUNT(*) AS n FROM jobs WHERE user_id=? AND kind='link' AND created>?",
                      (u["id"], now() - 86400))["n"]
        if used >= ent["per_day"]:
            return RedirectResponse("/app?notice=" + urllib.parse.quote(
                f"Fair-use limit reached ({ent['per_day']} videos per 24 h on {ent['label']})."), 303)
        clen = int(req.headers.get("content-length") or 0)
        if clen > cfg.max_upload_bytes + 1_000_000:
            raise HTTPException(413, "File too large")
        form = await req.form(max_files=1, max_fields=40, max_part_size=64 * 1024)
        try:
            return await _create_job(req, s, u, ent, form)
        finally:
            await form.close()

    LAYOUTS = ("smart", "facecam", "blurfit", "crop", "fit")

    async def _create_job(req: Request, s: dict, u: dict, ent: dict, form):
        if not security.csrf_ok(s, form.get("csrf")):
            raise HTTPException(403, "Invalid or expired form. Reload the page and try again.")
        ratios = [r for r in form.getlist("ratios") if r in RATIOS] or ["9:16"]
        style = form.get("caption_style") if form.get("caption_style") in STYLES else "bold_pop"
        try:
            count = max(1, min(30, int(form.get("count") or 5)))
        except ValueError:
            count = 5
        basis = form.get("rights_basis") or ""
        if basis and basis not in APPROVED_LICENSES:
            raise HTTPException(400, "Invalid rights basis")
        spec = {"ratios": ratios, "caption_style": style, "count": count, "cold_open": form.get("cold_open") == "on",
                "rights_basis": basis, "rights_evidence": (form.get("rights_evidence") or "")[:500],
                "max_seconds": ent["max_minutes"] * 60, "max_bytes": cfg.max_upload_bytes,
                "category": (form.get("category") or "")[:40],
                "detect": form.get("detect") if form.get("detect") in ("auto", "speech", "action") else "auto",
                "clip_length": form.get("clip_length") if form.get("clip_length") in ("auto", "30", "45", "60") else "auto",
                "layout": form.get("layout") if form.get("layout") in LAYOUTS else "smart",
                "progress_bar": form.get("progress_bar") == "on"}
        if spec["layout"] == "facecam" and form.get("facecam_pos") in ("tl", "tr", "bl", "br"):
            spec["layout"] = "facecam_" + form.get("facecam_pos")
        weights = {}
        for k in ("loud", "sudden", "motion", "cuts"):
            try:
                weights[k] = max(0.0, min(3.0, float(form.get("w_" + k))))
            except (TypeError, ValueError):
                pass
        spec["action_weights"] = weights
        if form.get("attest") != "on":
            return RedirectResponse("/app?notice=" + urllib.parse.quote("Please confirm you hold the rights."), 303)
        upload = form.get("file")
        url = (form.get("url") or "").strip()
        from ..ingest import IngestError, finalize_media, validate_public_url, youtube_id
        try:
            if upload is not None and getattr(upload, "filename", ""):
                ext = Path(upload.filename).suffix.lower()
                if ext not in {".mp4", ".mov", ".mkv", ".webm", ".m4v"}:
                    raise IngestError("Upload an .mp4, .mov, .mkv, .webm or .m4v file")
                inbox = user_dir(cfg.data_dir, u["id"]) / "inbox"
                inbox.mkdir(parents=True, exist_ok=True)
                dest = inbox / (secrets.token_hex(8) + ".upload")
                size = 0
                with dest.open("wb") as f:
                    while chunk := await upload.read(1 << 20):
                        size += len(chunk)
                        if size > cfg.max_upload_bytes:
                            f.close()
                            dest.unlink(missing_ok=True)
                            raise IngestError("File too large for your plan")
                        f.write(chunk)
                spec["upload_path"] = str(await run_in_threadpool(finalize_media, dest))
                spec["upload_name"] = Path(upload.filename).name[:120]
            elif url:
                if not youtube_id(url):
                    await run_in_threadpool(validate_public_url, url)
                else:   # check rights now so the user gets an answer immediately, not a failed job later
                    from ..ingest import LiveNotFinished, youtube_info, youtube_rights
                    channels = {r["channel_id"] for r in db.all("SELECT channel_id FROM channels WHERE user_id=?", (u["id"],))}
                    info = await run_in_threadpool(youtube_info, youtube_id(url))
                    try:
                        youtube_rights(info, channels)
                    except LiveNotFinished:
                        pass                    # queue it; the worker starts when the replay exists
                spec["url"] = url
            else:
                raise IngestError("Paste a link or choose a file")
            if not youtube_id(url or "") and basis == "original" and len(spec["rights_evidence"]) < 8:
                # own recordings (OBS, console capture, phone): the signed attestation is the evidence
                spec["rights_evidence"] = (f"Uploader attests this is their own recording ({u['email']}, "
                                           f"{time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())})")
            if not youtube_id(url or "") and (basis not in APPROVED_LICENSES or len(spec["rights_evidence"]) < 8):
                raise IngestError("For uploads and direct links, choose your rights basis and describe the evidence.")
        except IngestError as exc:
            return RedirectResponse("/app?notice=" + urllib.parse.quote(str(exc)), 303)
        jid = enqueue(db, u["id"], "link", spec)
        return RedirectResponse(f"/app/jobs/{jid}", 303)

    async def job_page(req: Request):
        s, u = require_user(req)
        job = db.one("SELECT * FROM jobs WHERE id=? AND user_id=? AND kind='link'", (req.path_params["jid"], u["id"]))
        if not job:
            raise HTTPException(404)
        outs = db.all("SELECT * FROM outputs WHERE job_id=? AND user_id=? ORDER BY id", (job["id"], u["id"]))
        for o in outs:
            try:
                meta = json.loads(Path(o["meta_file"]).read_text(encoding="utf-8"))
                o["hook"], o["score"] = meta.get("hook_text", ""), meta.get("originality_score")
                o["scores"] = meta.get("scores") or {}
                o["has_poster"] = bool(meta.get("poster"))
                o["social"] = meta.get("social") or {}
            except (OSError, ValueError):
                o["hook"], o["score"], o["scores"], o["has_poster"], o["social"] = "", None, {}, False, {}
        for o in outs:
            try:
                m = json.loads(Path(o["meta_file"]).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                m = {}
            o["language"], o["dubbed"], o["take"] = m.get("language", ""), bool(m.get("dubbed_from")), bool(m.get("added_commentary"))
            o["posts"] = db.all("SELECT platform, handle, status, url, error FROM posts WHERE user_id=? AND output_id=? "
                                "ORDER BY created DESC", (u["id"], o["id"]))
        outs.sort(key=lambda o: (-(o["scores"].get("overall") or 0), o["id"]))
        from ..transform import LANGS, available_languages
        from .publishing import accounts
        langs = available_languages()
        spec = json.loads(job["spec"])
        return page(req, "job.html", {"job": job, "outputs": outs, "label": spec.get("url") or spec.get("upload_name"),
                                      "accounts": accounts(db, u["id"]), "langs": LANGS, "langs_ready": langs})

    async def api_job(req: Request):
        s, u = require_user(req)
        job = db.one("SELECT id,status,progress,error FROM jobs WHERE id=? AND user_id=?", (req.path_params["jid"], u["id"]))
        if not job:
            raise HTTPException(404)
        return JSONResponse(job)

    async def delete_job(req: Request):
        s, u = require_user(req)
        await form_checked(req, s)
        jid = req.path_params["jid"]
        job = db.one("SELECT * FROM jobs WHERE id=? AND user_id=? AND status IN ('done','failed','waiting','queued')",
                     (jid, u["id"]))
        if job:
            with db.tx() as c:
                c.execute("DELETE FROM outputs WHERE job_id=? AND user_id=?", (jid, u["id"]))
                c.execute("DELETE FROM jobs WHERE id=? AND user_id=?", (jid, u["id"]))
            shutil.rmtree(job_dir(cfg.data_dir, u["id"], jid), ignore_errors=True)
        return RedirectResponse("/app", 303)

    # ---------------------------------------------------------------- review editor
    async def review(req: Request):
        s, u = require_user(req)
        out = owned_output(u, req.path_params["oid"])
        return page(req, "review.html", {"out": out, "styles": list(STYLES)})

    def _clip_state(out: dict) -> dict:
        meta = json.loads(Path(out["meta_file"]).read_text(encoding="utf-8"))
        srt = Path(out["file"]).with_suffix(".srt")
        words = []
        if srt.is_file():
            from ..transcripts import parse_srt
            words = parse_srt(srt.read_text(encoding="utf-8"))
        caps = [{"start": round(g[0][0], 2), "end": round(g[-1][1], 2), "text": " ".join(w[2] for w in g)}
                for g in group_words(words, 4)]
        return {"id": out["id"], "title": meta.get("title", ""), "hook": meta.get("hook_text", ""),
                "caption_style": meta.get("caption_style", "bold_pop"), "cold_open": meta.get("cold_open_seconds", 0) > 0,
                "cold_open_seconds": meta.get("cold_open_seconds", 0), "range": meta.get("range"), "ratio": meta.get("ratio"),
                "duration": meta.get("duration_seconds"), "captions": caps,
                "source_duration": meta.get("source_duration"), "layout": meta.get("layout", "smart"),
                "progress_bar": bool(meta.get("progress_bar")),
                "edit": meta.get("edit") or {"speed": 1.0, "fade_in": 0.0, "fade_out": 0.0, "volume_db": 0.0}}

    async def api_output(req: Request):
        s, u = require_user(req)
        return JSONResponse(await run_in_threadpool(_clip_state, owned_output(u, req.path_params["oid"])))

    def _override(body: dict, meta_range: list[float]) -> dict:
        ov: dict = {}
        if "start" in body and "end" in body:
            a, b = float(body["start"]), float(body["end"])
            lo, hi = meta_range[0] - 30, meta_range[1] + 30          # editor may extend 30 s either side
            if not (max(0, lo) <= a < b <= hi and 3 <= b - a <= 180):
                raise HTTPException(400, "Trim must stay within 30 s of the original clip and last 3-180 s")
            ov["start"], ov["end"] = a, b
        if isinstance(body.get("captions"), list):
            caps = body["captions"][:400]
            ov["captions"] = [{"start": float(c["start"]), "end": float(c["end"]), "text": str(c["text"])[:200]}
                              for c in caps if isinstance(c, dict) and {"start", "end", "text"} <= set(c)]
        if "hook" in body:
            ov["hook"] = str(body["hook"])[:120]
        if body.get("caption_style") in STYLES:
            ov["caption_style"] = body["caption_style"]
        if "cold_open" in body:
            ov["cold_open"] = bool(body["cold_open"])
        if body.get("title"):
            ov["title"] = str(body["title"])[:100]
        try:
            if "speed" in body:
                ov["speed"] = max(0.5, min(2.0, float(body["speed"])))
            for k, hi in (("fade_in", 2.0), ("fade_out", 2.0)):
                if k in body:
                    ov[k] = max(0.0, min(hi, float(body[k])))
            if "volume_db" in body:
                ov["volume_db"] = max(-12.0, min(12.0, float(body["volume_db"])))
        except (TypeError, ValueError):
            raise HTTPException(400, "Invalid speed, fade or volume")
        lay = str(body.get("layout") or "")
        if lay in LAYOUTS or lay in ("facecam_tl", "facecam_tr", "facecam_bl", "facecam_br"):
            ov["layout"] = lay
        if "progress_bar" in body:
            ov["progress_bar"] = bool(body["progress_bar"])
        return ov

    async def api_preview(req: Request):
        s, u = require_user(req)
        api_checked(req, s)
        if security.rate_limited("preview:" + u["id"], 20, 600):
            raise HTTPException(429, "Too many previews, wait a few minutes")
        out = owned_output(u, req.path_params["oid"])
        body = await req.json()
        meta = json.loads(Path(out["meta_file"]).read_text(encoding="utf-8"))
        ov = _override(body if isinstance(body, dict) else {}, meta["range"])
        from ..clipper import rerender, timeline_captions
        res = await run_in_threadpool(rerender, out["id"], ov, Path(out["meta_file"]).parent, True, False)
        a, b = ov.get("start", meta["range"][0]), ov.get("end", meta["range"][1])
        cold = ov.get("cold_open", meta.get("cold_open_seconds", 0) > 0)
        caps, qd = await run_in_threadpool(timeline_captions, meta, a, b, cold)
        return JSONResponse({"url": f"/media/{out['id']}/preview?v={secrets.token_hex(4)}", "duration": res["duration_seconds"],
                             "range": [a, b], "captions": caps, "cold_open_seconds": qd})

    async def api_render(req: Request):
        s, u = require_user(req)
        api_checked(req, s)
        out = owned_output(u, req.path_params["oid"])
        if db.one("SELECT id FROM jobs WHERE user_id=? AND kind='rerender' AND status IN ('queued','running') "
                  "AND spec LIKE ?", (u["id"], f'%"{out["id"]}"%')):
            raise HTTPException(409, "A render for this clip is already running")
        body = await req.json()
        meta = json.loads(Path(out["meta_file"]).read_text(encoding="utf-8"))
        jid = enqueue(db, u["id"], "rerender", {"output_id": out["id"], "override": _override(body, meta["range"])})
        return JSONResponse({"job": jid})

    async def media(req: Request):
        s, u = require_user(req)
        out = owned_output(u, req.path_params["oid"])
        variant = req.path_params["variant"]
        f = Path(out["file"])
        path = {"mp4": f, "preview": f.with_name(f.stem + ".preview.mp4"), "srt": f.with_suffix(".srt"),
                "poster": f.with_suffix(".jpg")}.get(variant)
        if not path or not path.is_file():
            raise HTTPException(404)
        headers = {}
        if req.query_params.get("download") == "1":
            name = "".join(ch for ch in (out["title"] or out["id"]) if ch.isalnum() or ch in " -_")[:60].strip() or out["id"]
            headers["content-disposition"] = f'attachment; filename="{name}{path.suffix}"'
        mt = {".mp4": "video/mp4", ".jpg": "image/jpeg"}.get(path.suffix, "text/plain; charset=utf-8")
        return FileResponse(path, media_type=mt, headers=headers)


    # ---------------------------------------------------------------- social accounts & publishing
    async def social_connect(req: Request):
        s, u = require_user(req)
        await form_checked(req, s)
        from ..social.http import PlatformError
        from .publishing import oauth_start
        try:
            url, state = oauth_start(req.path_params["platform"], cfg.base_url)
        except PlatformError as exc:
            return RedirectResponse("/account?msg=" + urllib.parse.quote(str(exc)), 303)
        from itsdangerous import URLSafeTimedSerializer
        resp = RedirectResponse(url, 303)
        resp.set_cookie("tt_social", URLSafeTimedSerializer(cfg.secret_key, "social").dumps({**state, "u": u["id"]}),
                        max_age=600, httponly=True, secure=cfg.secure_cookies, samesite="lax", path="/social")
        return resp

    async def social_callback(req: Request):
        s, u = require_user(req)
        platform = req.path_params["platform"]
        from itsdangerous import BadSignature, URLSafeTimedSerializer
        try:
            data = URLSafeTimedSerializer(cfg.secret_key, "social").loads(req.cookies.get("tt_social", ""), max_age=600)
        except BadSignature:
            raise HTTPException(400, "Connection expired; try again")
        if data["u"] != u["id"] or data["p"] != platform or not secrets.compare_digest(data["s"], req.query_params.get("state", "")):
            raise HTTPException(400, "OAuth state mismatch")
        if req.query_params.get("error"):
            return RedirectResponse("/account?msg=" + urllib.parse.quote("Connection cancelled"), 303)
        from ..social.http import PlatformError
        from .publishing import adapter, save_account
        try:
            acct = await run_in_threadpool(adapter(platform).exchange, req.query_params.get("code", ""),
                                           f"{cfg.base_url}/social/callback/{platform}", data["v"])
            save_account(db, u["id"], platform, acct)
            msg = f"Connected {acct.get('handle') or platform}"
        except PlatformError as exc:
            msg = f"Couldn't connect: {exc}"
        resp = RedirectResponse("/account?msg=" + urllib.parse.quote(msg[:300]), 303)
        resp.delete_cookie("tt_social", path="/social")
        return resp

    async def social_disconnect(req: Request):
        s, u = require_user(req)
        form = await form_checked(req, s)
        db.run("DELETE FROM social_accounts WHERE id=? AND user_id=?", (form.get("id", ""), u["id"]))
        return RedirectResponse("/account?msg=" + urllib.parse.quote(
            "Disconnected. To fully revoke access, also remove the app in that platform's security settings."), 303)

    async def api_publish(req: Request):
        s, u = require_user(req)
        api_checked(req, s)
        if not billing.entitlement(db, u["id"]):
            raise HTTPException(402, "An active plan is required")
        from .publishing import validate_request
        try:
            spec = validate_request(db, u["id"], await req.json())
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        jid = enqueue(db, u["id"], "publish", spec)
        if spec.get("schedule_at"):
            db.run("UPDATE jobs SET not_before=?, progress='scheduled' WHERE id=?", (spec["schedule_at"], jid))
        return JSONResponse({"job": jid, "scheduled": bool(spec.get("schedule_at")), "at": spec.get("schedule_at")})

    # ---------------------------------------------------------------- content calendar
    async def calendar(req: Request):
        s, u = require_user(req)
        rows = db.all("SELECT id, status, progress, spec, created, not_before, error FROM jobs WHERE user_id=? AND "
                      "kind='publish' ORDER BY COALESCE(NULLIF(not_before,0), created) DESC LIMIT 500", (u["id"],))
        titles = {o["id"]: o["title"] for o in db.all("SELECT id, title FROM outputs WHERE user_id=?", (u["id"],))}
        items = []
        for r in rows:
            sp = json.loads(r["spec"])
            posts = db.all("SELECT platform, handle, status, url, error FROM posts WHERE user_id=? AND job_id=?",
                           (u["id"], r["id"]))
            items.append({"id": r["id"], "at": r["not_before"] or r["created"], "status": r["status"],
                          "scheduled": bool(sp.get("schedule_at")),
                          "clips": [{"id": i, "title": titles.get(i, "(deleted clip)")} for i in sp.get("output_ids", [])],
                          "platforms": sorted({t["platform"] for t in sp.get("targets", [])}),
                          "posts": posts, "error": r["error"]})
        return page(req, "calendar.html", {"items": items})

    async def api_calendar_cancel(req: Request):
        s, u = require_user(req)
        api_checked(req, s)
        with db.tx() as c:
            n = c.execute("UPDATE jobs SET status='cancelled', progress='cancelled', updated=? WHERE id=? AND user_id=? "
                          "AND kind='publish' AND status='queued'", (time.time(), req.path_params["jid"], u["id"])).rowcount
        if not n:
            raise HTTPException(409, "This post already started or isn't scheduled")
        return JSONResponse({"ok": True})

    async def api_transform(req: Request):
        s, u = require_user(req)
        api_checked(req, s)
        if not billing.entitlement(db, u["id"]):
            raise HTTPException(402, "An active plan is required")
        if security.rate_limited("transform:" + u["id"], 20, 3600):
            raise HTTPException(429, "Too many requests, try again later")
        out = owned_output(u, req.path_params["oid"])
        body = await req.json()
        kind = req.path_params["kind"]
        from ..transform import LANGS
        if kind == "translate":
            lang = str(body.get("lang", ""))
            if lang not in LANGS:
                raise HTTPException(400, "Unsupported language")
            jid = enqueue(db, u["id"], "dub", {"output_id": out["id"], "lang": lang})
        elif kind == "take":
            take = str(body.get("take", "")).strip()
            if not 8 <= len(take) <= 300:
                raise HTTPException(400, "Write your take in 8-300 characters")
            jid = enqueue(db, u["id"], "take", {"output_id": out["id"], "take": take})
        else:
            raise HTTPException(404)
        return JSONResponse({"job": jid})

    async def api_suggest_take(req: Request):
        s, u = require_user(req)
        out = owned_output(u, req.path_params["oid"])
        from ..transform import suggest_take
        meta = json.loads(Path(out["meta_file"]).read_text(encoding="utf-8"))
        return JSONResponse({"take": await run_in_threadpool(suggest_take, meta)})

    async def media_zip(req: Request):
        s, u = require_user(req)
        ids = [i for i in (req.query_params.get("ids") or "").split(",") if i][:50]
        if not ids:
            raise HTTPException(400, "No clips selected")
        files = []
        for i in ids:
            o = owned_output(u, i)
            f = Path(o["file"])
            if f.is_file():
                name = "".join(ch for ch in (o["title"] or o["id"]) if ch.isalnum() or ch in " -_")[:50].strip() or o["id"]
                files.append((f"{len(files) + 1:02d} {name} ({o['ratio'].replace(':', 'x')}).mp4", f))
        if not files:
            raise HTTPException(404)
        import tempfile
        from starlette.background import BackgroundTask
        from ..transform import zip_outputs
        tmp = Path(tempfile.mkstemp(suffix=".zip")[1])
        await run_in_threadpool(zip_outputs, files, tmp)
        return FileResponse(tmp, media_type="application/zip", filename=f"{BRAND.replace(' ', '')}-clips.zip",
                            background=BackgroundTask(tmp.unlink, missing_ok=True))

    # ---------------------------------------------------------------- billing
    async def billing_page(req: Request):
        s, u = require_user(req)
        return page(req, "billing.html", {"status": req.query_params.get("status", ""),
                                          "stripe_ready": bool(cfg.stripe_secret),
                                          "next": safe_next(req.query_params.get("next")),
                                          "need": req.query_params.get("need"), "welcome": req.query_params.get("welcome"),
                                          "sub": db.one("SELECT * FROM subscriptions WHERE user_id=?", (u["id"],))})

    async def billing_checkout(req: Request):
        s, u = require_user(req)
        form = await form_checked(req, s)
        try:
            url = await run_in_threadpool(billing.checkout_url, db, cfg.stripe_secret, cfg.base_url, u, form.get("plan", ""))
        except billing.BillingError as exc:
            return page(req, "billing.html", {"error": str(exc)}, 400)
        return RedirectResponse(url, 303)

    async def billing_dev_activate(req: Request):
        """DEVELOPMENT ONLY (TT_DEV=1): grant a 30-day plan without Stripe. 404 in production."""
        if not cfg.dev:
            raise HTTPException(404)
        s, u = require_user(req)
        form = await form_checked(req, s)
        plan = form.get("plan") if form.get("plan") in PLANS else "monthly"
        db.run("INSERT OR REPLACE INTO subscriptions VALUES (?,?,?,?,?,?)",
               (u["id"], "sub_dev", plan, "active", now() + 30 * 86400, now()))
        return RedirectResponse(safe_next(form.get("next")), 303)

    async def billing_portal(req: Request):
        s, u = require_user(req)
        await form_checked(req, s)
        if not u.get("stripe_customer"):
            return RedirectResponse("/billing", 303)
        try:
            url = await run_in_threadpool(billing.portal_url, cfg.stripe_secret, cfg.base_url, u["stripe_customer"])
        except billing.BillingError as exc:
            return page(req, "billing.html", {"error": str(exc)}, 400)
        return RedirectResponse(url, 303)

    async def stripe_webhook(req: Request):
        if int(req.headers.get("content-length") or 0) > 512 * 1024:
            raise HTTPException(413)
        payload = await req.body()
        try:
            event = billing.verify_signature(payload, req.headers.get("stripe-signature", ""), cfg.stripe_webhook_secret)
        except (billing.BillingError, ValueError):
            return JSONResponse({"error": "invalid signature"}, 400)
        result = await run_in_threadpool(billing.handle_event, db, event)
        return JSONResponse({"received": True, "result": result})

    # ---------------------------------------------------------------- account & YouTube channel verification
    async def account(req: Request):
        s, u = require_user(req)
        msg = None
        if req.method == "POST":
            form = await form_checked(req, s)
            if form.get("action") == "password":
                if not security.verify_password(form.get("current", ""), u["pw_hash"]):
                    msg = "Current password is incorrect."
                elif err := security.password_problem(form.get("new", ""), u["email"]):
                    msg = err
                else:
                    db.run("UPDATE users SET pw_hash=? WHERE id=?", (security.hash_password(form.get("new")), u["id"]))
                    security.destroy_all_sessions(db, u["id"])
                    resp = RedirectResponse("/login?next=/account", 303)
                    resp.delete_cookie(security.SESSION_COOKIE, path="/")
                    return resp
            elif form.get("action") == "remove_channel":
                db.run("DELETE FROM channels WHERE user_id=? AND channel_id=?", (u["id"], form.get("channel_id", "")))
                msg = "Channel removed."
        channels = db.all("SELECT * FROM channels WHERE user_id=?", (u["id"],))
        from .publishing import accounts, adapter
        socials = accounts(db, u["id"])
        platforms = [{"key": k, "label": adapter(k).label, "configured": adapter(k).configured(),
                      "accounts": [a for a in socials if a["platform"] == k]} for k in ("youtube", "instagram", "tiktok")]
        msg = msg or req.query_params.get("msg")
        return page(req, "account.html", {"channels": channels, "msg": msg, "platforms": platforms,
                                          "google": Path(cfg.google_web_client).is_file() or (Path.cwd() / cfg.google_web_client).is_file()})

    def _google_flow(state: str | None = None):
        from google_auth_oauthlib.flow import Flow
        from ..common import ROOT
        path = Path(cfg.google_web_client)
        path = path if path.is_absolute() else ROOT / path
        flow = Flow.from_client_secrets_file(str(path), scopes=["https://www.googleapis.com/auth/youtube.readonly"],
                                             state=state, redirect_uri=f"{cfg.base_url}/connect/youtube/callback")
        return flow

    async def connect_youtube(req: Request):
        s, u = require_user(req)
        await form_checked(req, s)
        flow = _google_flow()
        flow.code_verifier = secrets.token_urlsafe(64)
        url, state = flow.authorization_url(access_type="online", include_granted_scopes="true", prompt="select_account")
        resp = RedirectResponse(url, 303)
        from itsdangerous import URLSafeTimedSerializer
        token = URLSafeTimedSerializer(cfg.secret_key, "yt-oauth").dumps({"s": state, "v": flow.code_verifier, "u": u["id"]})
        resp.set_cookie("tt_oauth", token, max_age=600, httponly=True, secure=cfg.secure_cookies, samesite="lax",
                        path="/connect/youtube")
        return resp

    async def connect_youtube_callback(req: Request):
        s, u = require_user(req)
        from itsdangerous import BadSignature, URLSafeTimedSerializer
        try:
            data = URLSafeTimedSerializer(cfg.secret_key, "yt-oauth").loads(req.cookies.get("tt_oauth", ""), max_age=600)
        except BadSignature:
            raise HTTPException(400, "OAuth session expired; try again")
        if data["u"] != u["id"] or not secrets.compare_digest(data["s"], req.query_params.get("state", "")):
            raise HTTPException(400, "OAuth state mismatch")

        def finish():
            flow = _google_flow(data["s"])
            flow.code_verifier = data["v"]
            flow.fetch_token(code=req.query_params.get("code", ""))
            from googleapiclient.discovery import build
            yt = build("youtube", "v3", credentials=flow.credentials, cache_discovery=False)
            return yt.channels().list(part="snippet", mine=True, maxResults=50).execute().get("items", [])
        items = await run_in_threadpool(finish)
        for ch in items:     # Google token is used once and discarded; we keep only the verified channel IDs
            db.run("INSERT OR REPLACE INTO channels VALUES (?,?,?,?)", (u["id"], ch["id"], ch["snippet"]["title"][:120], now()))
        resp = RedirectResponse("/account", 303)
        resp.delete_cookie("tt_oauth", path="/connect/youtube")
        return resp

    # ---------------------------------------------------------------- errors & pre-CSRF seed
    async def http_error(req: Request, exc: HTTPException):
        if exc.status_code in (301, 302, 303) and exc.headers:
            return RedirectResponse(exc.headers["Location"], exc.status_code)
        if req.url.path.startswith("/api/"):
            return JSONResponse({"error": exc.detail}, exc.status_code)
        return page(req, "error.html", {"code": exc.status_code, "detail": exc.detail}, exc.status_code)

    async def server_error(req: Request, exc: Exception):
        return page(req, "error.html", {"code": 500, "detail": "Something went wrong."}, 500)

    class PreCsrfSeed:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope["type"] != "http":
                return await self.app(scope, receive, send)
            headers = list(scope.get("headers", []))
            has = any(k == b"cookie" and b"tt_pre=" in v for k, v in headers)
            if not has:
                seed = secrets.token_urlsafe(24)
                idx = next((i for i, (k, _) in enumerate(headers) if k == b"cookie"), None)
                if idx is None:
                    headers.append((b"cookie", f"tt_pre={seed}".encode()))
                else:
                    headers[idx] = (b"cookie", headers[idx][1] + f"; tt_pre={seed}".encode())
                scope["headers"] = headers
                flags = "; HttpOnly; SameSite=Lax; Path=/" + ("; Secure" if cfg.secure_cookies else "")

                async def _send(m):
                    if m["type"] == "http.response.start":
                        m["headers"] = list(m.get("headers", [])) + [(b"set-cookie", f"tt_pre={seed}{flags}".encode())]
                    await send(m)
                return await self.app(scope, receive, _send)
            return await self.app(scope, receive, send)

    routes = [
        Route("/", landing), Route("/start", start), Route("/legal/{doc}", legal), Route("/healthz", healthz), Route("/favicon.ico", favicon),
        Route("/signup", signup, methods=["GET", "POST"]), Route("/login", login, methods=["GET", "POST"]),
        Route("/logout", logout, methods=["POST"]),
        Route("/app", dashboard), Route("/app/jobs", create_job, methods=["POST"]),
        Route("/app/jobs/{jid:str}", job_page), Route("/app/jobs/{jid:str}/delete", delete_job, methods=["POST"]),
        Route("/app/review/{oid:str}", review), Route("/app/calendar", calendar),
        Route("/api/calendar/{jid:str}/cancel", api_calendar_cancel, methods=["POST"]),
        Route("/api/jobs/{jid:str}", api_job), Route("/api/outputs/{oid:str}", api_output),
        Route("/api/outputs/{oid:str}/preview", api_preview, methods=["POST"]),
        Route("/api/outputs/{oid:str}/render", api_render, methods=["POST"]),
        Route("/media/zip", media_zip),
        Route("/media/{oid:str}/{variant:str}", media),
        Route("/social/connect/{platform:str}", social_connect, methods=["POST"]),
        Route("/social/callback/{platform:str}", social_callback),
        Route("/social/disconnect", social_disconnect, methods=["POST"]),
        Route("/api/publish", api_publish, methods=["POST"]),
        Route("/api/outputs/{oid:str}/suggest-take", api_suggest_take),
        Route("/api/outputs/{oid:str}/do/{kind:str}", api_transform, methods=["POST"]),
        Route("/billing", billing_page), Route("/billing/checkout", billing_checkout, methods=["POST"]),
        Route("/billing/portal", billing_portal, methods=["POST"]),
        Route("/billing/dev-activate", billing_dev_activate, methods=["POST"]),
        Route("/stripe/webhook", stripe_webhook, methods=["POST"]),
        Route("/account", account, methods=["GET", "POST"]),
        Route("/connect/youtube", connect_youtube, methods=["POST"]),
        Route("/connect/youtube/callback", connect_youtube_callback),
        Mount("/static", StaticFiles(directory=str(HERE / "static")), name="static"),
    ]
    app = Starlette(debug=False, routes=routes,
                    middleware=[Middleware(TrustedHostMiddleware, allowed_hosts=cfg.allowed_hosts),
                                Middleware(SecurityHeaders, secure=cfg.secure_cookies), Middleware(PreCsrfSeed)],
                    exception_handlers={HTTPException: http_error, 500: server_error})
    app.state.db, app.state.cfg = db, cfg
    if start_workers and cfg.workers > 0:
        app.state.stop = start_threads(db.path, cfg.data_dir, cfg.workers)
    return app
