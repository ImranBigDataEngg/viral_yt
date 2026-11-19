"""TrendTide web app. Run: trendtide web  (or: uvicorn viral_yt.web.app:create_app --factory)

Security model (see docs/WEB_SECURITY.md): hashed server-side sessions in HttpOnly/SameSite cookies, CSRF tokens on
every state change + Origin check, scrypt passwords, persistent login throttling, strict CSP (no inline JS),
per-user authorisation on every object, SSRF-safe ingest, upload size/type validation, Stripe-hosted payments with
signed webhooks as the only source of entitlements.
"""
from __future__ import annotations

import json
import secrets
import shutil
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


def create_app(cfg: WebConfig | None = None, start_workers: bool = True) -> Starlette:
    cfg = cfg or load()
    db = DB(cfg.data_dir / "web.db")
    origin = "{0.scheme}://{0.netloc}".format(urllib.parse.urlsplit(cfg.base_url))

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
        base = {"user": u, "csrf": s["csrf"] if s else "", "ent": ent, "plans": PLANS, "dev": cfg.dev}
        return templates.TemplateResponse(req, name, {**base, **(ctx or {})}, status_code=status)

    async def form_checked(req: Request, session: dict) -> dict:
        if req.headers.get("origin") not in (None, origin):
            raise HTTPException(403, "Bad origin")
        form = await req.form(max_files=0, max_fields=40)
        if not security.csrf_ok(session, form.get("csrf")):
            raise HTTPException(403, "Invalid or expired form. Reload the page and try again.")
        return form

    def api_checked(req: Request, session: dict) -> None:
        if req.headers.get("origin") not in (None, origin) or not security.csrf_ok(session, req.headers.get("x-csrf-token")):
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
    async def landing(req: Request):
        return page(req, "landing.html")

    async def legal(req: Request):
        doc = req.path_params["doc"]
        if doc not in ("terms", "privacy", "acceptable-use"):
            raise HTTPException(404)
        return page(req, "legal.html", {"doc": doc})

    async def healthz(req: Request):
        return JSONResponse({"ok": True})

    async def favicon(req: Request):
        return FileResponse(HERE / "static" / "favicon.svg", media_type="image/svg+xml")

    # ---------------------------------------------------------------- auth
    async def signup(req: Request):
        if req.method == "GET":
            return page(req, "auth.html", {"mode": "signup", "csrf": _pre_csrf(req)})
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
            return page(req, "auth.html", {"mode": "signup", "error": err, "email": email or "", "csrf": _pre_csrf(req)}, 400)
        uid = secrets.token_hex(12)
        db.run("INSERT INTO users (id,email,pw_hash,created,accepted_terms) VALUES (?,?,?,?,?)",
               (uid, email, security.hash_password(pw), now(), now()))
        resp = RedirectResponse("/billing?welcome=1", 303)
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
        channels = db.all("SELECT channel_id,title FROM channels WHERE user_id=?", (u["id"],))
        return page(req, "dashboard.html", {"jobs": jobs, "channels": channels, "ratios": list(RATIOS),
                                             "styles": list(STYLES), "bases": BASIS_LABELS,
                                             "notice": req.query_params.get("notice", "")})

    async def create_job(req: Request):
        s, u = require_user(req)
        if req.headers.get("origin") not in (None, origin):
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
        form = await req.form(max_files=1, max_fields=20, max_part_size=64 * 1024)
        try:
            return await _create_job(req, s, u, ent, form)
        finally:
            await form.close()

    async def _create_job(req: Request, s: dict, u: dict, ent: dict, form):
        if not security.csrf_ok(s, form.get("csrf")):
            raise HTTPException(403, "Invalid or expired form. Reload the page and try again.")
        ratios = [r for r in form.getlist("ratios") if r in RATIOS] or ["9:16"]
        style = form.get("caption_style") if form.get("caption_style") in STYLES else "bold_pop"
        try:
            count = max(1, min(10, int(form.get("count") or 5)))
        except ValueError:
            count = 5
        basis = form.get("rights_basis") or ""
        if basis and basis not in APPROVED_LICENSES:
            raise HTTPException(400, "Invalid rights basis")
        spec = {"ratios": ratios, "caption_style": style, "count": count, "cold_open": form.get("cold_open") == "on",
                "rights_basis": basis, "rights_evidence": (form.get("rights_evidence") or "")[:500],
                "max_seconds": ent["max_minutes"] * 60, "max_bytes": cfg.max_upload_bytes,
                "category": (form.get("category") or "")[:40]}
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
                spec["url"] = url
            else:
                raise IngestError("Paste a link or choose a file")
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
            except (OSError, ValueError):
                o["hook"], o["score"] = "", None
        spec = json.loads(job["spec"])
        return page(req, "job.html", {"job": job, "outputs": outs, "label": spec.get("url") or spec.get("upload_name")})

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
                "source_duration": meta.get("source_duration")}

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
        path = {"mp4": f, "preview": f.with_name(f.stem + ".preview.mp4"), "srt": f.with_suffix(".srt")}.get(variant)
        if not path or not path.is_file():
            raise HTTPException(404)
        headers = {}
        if req.query_params.get("download") == "1":
            name = "".join(ch for ch in (out["title"] or out["id"]) if ch.isalnum() or ch in " -_")[:60].strip() or out["id"]
            headers["content-disposition"] = f'attachment; filename="{name}{path.suffix}"'
        mt = "video/mp4" if path.suffix == ".mp4" else "text/plain; charset=utf-8"
        return FileResponse(path, media_type=mt, headers=headers)

    # ---------------------------------------------------------------- billing
    async def billing_page(req: Request):
        s, u = require_user(req)
        return page(req, "billing.html", {"status": req.query_params.get("status", ""),
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
        return page(req, "account.html", {"channels": channels, "msg": msg,
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
        Route("/", landing), Route("/legal/{doc}", legal), Route("/healthz", healthz), Route("/favicon.ico", favicon),
        Route("/signup", signup, methods=["GET", "POST"]), Route("/login", login, methods=["GET", "POST"]),
        Route("/logout", logout, methods=["POST"]),
        Route("/app", dashboard), Route("/app/jobs", create_job, methods=["POST"]),
        Route("/app/jobs/{jid:str}", job_page), Route("/app/jobs/{jid:str}/delete", delete_job, methods=["POST"]),
        Route("/app/review/{oid:str}", review),
        Route("/api/jobs/{jid:str}", api_job), Route("/api/outputs/{oid:str}", api_output),
        Route("/api/outputs/{oid:str}/preview", api_preview, methods=["POST"]),
        Route("/api/outputs/{oid:str}/render", api_render, methods=["POST"]),
        Route("/media/{oid:str}/{variant:str}", media),
        Route("/billing", billing_page), Route("/billing/checkout", billing_checkout, methods=["POST"]),
        Route("/billing/portal", billing_portal, methods=["POST"]),
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
