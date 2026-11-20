"""End-to-end web tests over real HTTP (uvicorn in a thread): auth, CSRF, IDOR, billing webhooks, SSRF,
throttling, link/upload job -> clips -> review preview -> edited re-render."""
import hashlib
import hmac
import http.cookiejar
import importlib.util
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HAVE = all(importlib.util.find_spec(m) for m in ("starlette", "uvicorn", "jinja2", "multipart", "itsdangerous"))
WEBHOOK_SECRET = "whsec_test_" + "x" * 24


def _port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


class Client:
    def __init__(self, base):
        self.base = base
        self.jar = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar), NoRedirect)

    def req(self, method, path, data=None, headers=None):
        r = urllib.request.Request(self.base + path, data=data, method=method, headers=headers or {})
        try:
            with self.op.open(r, timeout=120) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def get(self, path):
        return self.req("GET", path)

    def form(self, path, fields, headers=None):
        return self.req("POST", path, urllib.parse.urlencode(fields).encode(),
                        {"Content-Type": "application/x-www-form-urlencoded", **(headers or {})})

    def multipart(self, path, fields, files):
        b = "----tt" + uuid.uuid4().hex
        body = b""
        for k, v in fields:
            body += f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
        for k, (name, data) in files.items():
            body += (f'--{b}\r\nContent-Disposition: form-data; name="{k}"; filename="{name}"\r\n'
                     f"Content-Type: video/mp4\r\n\r\n").encode() + data + b"\r\n"
        body += f"--{b}--\r\n".encode()
        return self.req("POST", path, body, {"Content-Type": f"multipart/form-data; boundary={b}"})

    def csrf(self, path):
        _, _, html = self.get(path)
        m = re.search(rb'name="csrf" value="([^"]+)"', html)
        return m.group(1).decode() if m else ""


def signed(payload: bytes, ts=None, secret=WEBHOOK_SECRET):
    ts = ts or int(time.time())
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    return {"Stripe-Signature": f"t={ts},v1={sig}", "Content-Type": "application/json"}


@unittest.skipUnless(HAVE and shutil.which("ffmpeg"), "web deps or ffmpeg missing")
class TestWeb(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="tt_web_"))
        os.environ.update({"TT_DEV": "1", "TT_DATA_DIR": str(cls.tmp / "data"), "TT_WORKERS": "1",
                           "STRIPE_WEBHOOK_SECRET": WEBHOOK_SECRET, "STRIPE_PRICE_MONTHLY": "price_test_monthly",
                           "TT_SECRET_KEY": "k" * 48, "TT_SIGNUPS_PER_IP_HOUR": "50"})
        os.environ["TRENDTIDE_ROOT"] = str(cls.tmp)
        shutil.copytree(REPO / "config", cls.tmp / "config", dirs_exist_ok=True)
        sys.path.insert(0, str(REPO / "src"))
        for mod in [m for m in sys.modules if m.startswith("viral_yt")]:
            del sys.modules[mod]
        import uvicorn
        from viral_yt.web.app import create_app
        port = _port()
        os.environ["TT_BASE_URL"] = f"http://127.0.0.1:{port}"
        cls.app = create_app()
        cls.server = uvicorn.Server(uvicorn.Config(cls.app, host="127.0.0.1", port=port, log_level="warning"))
        threading.Thread(target=cls.server.run, daemon=True).start()
        for _ in range(100):
            if cls.server.started:
                break
            time.sleep(0.05)
        cls.base = f"http://127.0.0.1:{port}"
        # small original test video with speech-like audio + a cached transcript (no ASR needed in CI)
        src = cls.tmp / "talk.mp4"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=1280x720:r=30:d=40",
                        "-f", "lavfi", "-i", "sine=f=200:d=40", "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
                        "-c:a", "aac", str(src)], check=True)
        cls.video = src.read_bytes()
        from viral_yt.media_prep import fingerprint
        words, t = [], 0.0
        for i in range(17):
            for w in ("Why does this keep happening?" if i % 6 == 0 else f"Point {i} explains one more idea.").split():
                words.append([round(t, 2), round(t + 0.3, 2), w])
                t += 0.33
            t += 0.25
        cls.fp_words = words
        cls.fp = fingerprint(src)

    @classmethod
    def tearDownClass(cls):
        cls.server.should_exit = True
        if hasattr(cls.app.state, "stop"):
            cls.app.state.stop.set()

    def _user(self, email):
        c = Client(self.base)
        tok = c.csrf("/signup")
        st, h, _ = c.form("/signup", {"csrf": tok, "email": email, "password": "correct horse battery", "terms": "on"})
        self.assertEqual(st, 303, "signup should redirect")
        return c

    def _activate(self, uid):
        ev = {"id": "evt_" + uuid.uuid4().hex, "type": "customer.subscription.created", "data": {"object": {
            "id": "sub_" + uuid.uuid4().hex[:10], "customer": "cus_" + uid[:8], "status": "active",
            "metadata": {"user_id": uid}, "current_period_end": int(time.time()) + 30 * 86400,
            "items": {"data": [{"price": {"id": "price_test_monthly"}}]}}}}
        body = json.dumps(ev).encode()
        c = Client(self.base)
        return c.req("POST", "/stripe/webhook", body, signed(body)), body

    def _uid(self, email):
        return self.app.state.db.one("SELECT id FROM users WHERE email=?", (email,))["id"]

    def test_01_headers_and_public_pages(self):
        st, h, body = Client(self.base).get("/")
        self.assertEqual(st, 200)
        self.assertIn("script-src 'self'", h.get("content-security-policy", ""))
        self.assertEqual(h.get("x-frame-options"), "DENY")
        self.assertIn(b"$25", body)
        st, _, _ = Client(self.base).req("GET", "/", headers={"Host": "evil.example"})
        self.assertEqual(st, 400)  # trusted hosts

    def test_02_csrf_and_origin_enforced(self):
        c = self._user("csrf@example.com")
        st, _, _ = c.form("/logout", {"csrf": "wrong"})
        self.assertEqual(st, 403)
        tok = c.csrf("/app")
        st, _, _ = c.form("/logout", {"csrf": tok}, {"Origin": "https://evil.example"})
        self.assertEqual(st, 403)
        st, _, _ = Client(self.base).form("/signup", {"email": "x@example.com", "password": "correct horse battery", "terms": "on"})
        self.assertEqual(st, 403)  # pre-login CSRF

    def test_03_login_throttle_and_no_enumeration(self):
        self._user("throttle@example.com")
        c = Client(self.base)
        msgs = set()
        st, _, body = c.form("/login", {"csrf": c.csrf("/login"), "email": "ghost@example.com", "password": "nope-nope-nope"})
        msgs.add(re.search(rb'class="alert">([^<]+)', body).group(1))
        for _ in range(4):
            st, _, body = c.form("/login", {"csrf": c.csrf("/login"), "email": "throttle@example.com", "password": "nope-nope-nope"})
            self.assertEqual(st, 401)
            msgs.add(re.search(rb'class="alert">([^<]+)', body).group(1))
        self.assertEqual(len(msgs), 1)  # same message for unknown email
        st, _, _ = c.form("/login", {"csrf": c.csrf("/login"), "email": "throttle@example.com", "password": "correct horse battery"})
        self.assertEqual(st, 429)

    def test_04_webhook_signature_replay_and_entitlement(self):
        c = self._user("pay@example.com")
        st, _, _ = c.form("/app/jobs", {"csrf": c.csrf("/app"), "url": "https://example.com/a.mp4"})
        self.assertEqual(st, 303)  # -> billing (no plan)
        bad = Client(self.base).req("POST", "/stripe/webhook", b'{"id":"evt_x"}', signed(b'{"id":"evt_x"}', secret="whsec_wrong"))
        self.assertEqual(bad[0], 400)
        old = Client(self.base).req("POST", "/stripe/webhook", b'{"id":"evt_y"}', signed(b'{"id":"evt_y"}', ts=int(time.time()) - 3600))
        self.assertEqual(old[0], 400)
        (st, _, body), raw = self._activate(self._uid("pay@example.com"))
        self.assertEqual(st, 200)
        again = Client(self.base).req("POST", "/stripe/webhook", raw, signed(raw))
        self.assertIn(b"duplicate", again[2])
        _, _, page = c.get("/billing")
        self.assertIn(b"Monthly", page)
        self.assertIn(b"active", page)

    def test_04b_dev_activate_only_in_dev(self):
        c = self._user("devplan@example.com")
        _, _, page = c.get("/billing")
        self.assertIn(b"Activate free developer plan", page)
        st, h, _ = c.form("/billing/dev-activate", {"csrf": c.csrf("/billing"), "plan": "monthly"})
        self.assertEqual((st, h.get("location")), (303, "/app"))
        self.assertIn(b"Monthly", c.get("/app")[2])
        st, _, _ = c.form("/billing/dev-activate", {"csrf": "bad"})
        self.assertEqual(st, 403)
        self.app.state.cfg.dev = False
        try:
            st, _, _ = c.form("/billing/dev-activate", {"csrf": c.csrf("/billing")})
            self.assertEqual(st, 404)
        finally:
            self.app.state.cfg.dev = True

    def test_05_ssrf_blocked(self):
        c = self._user("ssrf@example.com")
        self._activate(self._uid("ssrf@example.com"))
        for url in ("http://127.0.0.1/x.mp4", "https://127.0.0.1/x.mp4", "https://user:pw@example.com/x.mp4",
                    "https://localhost/x.mp4", "https://[::1]/x.mp4", "https://169.254.169.254/latest"):
            st, h, _ = c.multipart("/app/jobs", [("csrf", c.csrf("/app")), ("url", url), ("attest", "on"),
                                                 ("rights_basis", "original"), ("rights_evidence", "my own video file")], {})
            self.assertEqual(st, 303)
            self.assertIn("/app?notice=", h.get("location", ""), url)

    def test_05b_gameplay_options_and_easy_rights(self):
        c = self._user("gamer@example.com")
        uid = self._uid("gamer@example.com")
        self._activate(uid)
        st, h, _ = c.multipart("/app/jobs", [("csrf", c.csrf("/app")), ("attest", "on"), ("rights_basis", "original"),
                                             ("detect", "action"), ("clip_length", "45"), ("layout", "facecam"),
                                             ("facecam_pos", "tr"), ("progress_bar", "on"), ("w_loud", "0"),
                                             ("w_motion", "2.5"), ("w_sudden", "9")],
                               {"file": ("stream.mp4", self.video)})
        self.assertEqual(st, 303, h)
        jid = h["location"].rsplit("/", 1)[1]
        spec = json.loads(self.app.state.db.one("SELECT spec FROM jobs WHERE id=?", (jid,))["spec"])
        self.assertEqual((spec["detect"], spec["clip_length"], spec["layout"], spec["progress_bar"]),
                         ("action", "45", "facecam_tr", True))
        self.assertEqual(spec["action_weights"], {"loud": 0.0, "sudden": 3.0, "motion": 2.5})   # clamped
        self.assertIn("gamer@example.com", spec["rights_evidence"])      # attestation recorded as evidence
        # bogus values fall back to safe defaults
        st, h, _ = c.multipart("/app/jobs", [("csrf", c.csrf("/app")), ("attest", "on"), ("rights_basis", "original"),
                                             ("detect", "rm -rf"), ("layout", "../../x"), ("clip_length", "9999")],
                               {"file": ("stream.mp4", self.video)})
        jid2 = h["location"].rsplit("/", 1)[1]
        spec2 = json.loads(self.app.state.db.one("SELECT spec FROM jobs WHERE id=?", (jid2,))["spec"])
        self.assertEqual((spec2["detect"], spec2["layout"], spec2["clip_length"]), ("auto", "smart", "auto"))
        for j in (jid, jid2):                                             # let the worker finish before later tests
            for _ in range(240):
                if json.loads(c.get(f"/api/jobs/{j}")[2])["status"] in ("done", "failed"):
                    break
                time.sleep(0.5)

    def test_06_job_review_rerender_and_idor(self):
        c = self._user("creator@example.com")
        uid = self._uid("creator@example.com")
        self._activate(uid)
        from viral_yt.common import ROOT
        wd = ROOT / "output" / "work" / self.fp
        wd.mkdir(parents=True, exist_ok=True)
        (wd / "words.json").write_text(json.dumps(self.fp_words))
        st, h, _ = c.multipart("/app/jobs", [("csrf", c.csrf("/app")), ("attest", "on"), ("rights_basis", "original"),
                                             ("rights_evidence", "my own screen recording"), ("count", "2"),
                                             ("ratios", "9:16"), ("ratios", "1:1"), ("cold_open", "on")],
                               {"file": ("talk.mp4", self.video)})
        self.assertEqual(st, 303, h)
        job_url = h["location"]
        jid = job_url.rsplit("/", 1)[1]
        for _ in range(240):
            j = json.loads(c.get(f"/api/jobs/{jid}")[2])
            if j["status"] in ("done", "failed"):
                break
            time.sleep(0.5)
        self.assertEqual(j["status"], "done", j)
        outs = self.app.state.db.all("SELECT * FROM outputs WHERE job_id=?", (jid,))
        self.assertGreaterEqual(len(outs), 2)
        self.assertEqual({o["ratio"] for o in outs}, {"9:16", "1:1"})
        oid = outs[0]["id"]
        st, _, body = c.get(f"/api/outputs/{oid}")
        state = json.loads(body)
        self.assertTrue(state["captions"])
        # preview requires CSRF header
        tok = c.csrf(f"/app/review/{oid}") or re.search(rb'data-csrf="([^"]+)"', c.get(f"/app/review/{oid}")[2]).group(1).decode()
        st, _, _ = c.req("POST", f"/api/outputs/{oid}/preview", b"{}", {"Content-Type": "application/json"})
        self.assertEqual(st, 403)
        a, b = state["range"]
        st, _, body = c.req("POST", f"/api/outputs/{oid}/preview", json.dumps({"start": a + 1, "end": b, "cold_open": False}).encode(),
                            {"Content-Type": "application/json", "X-CSRF-Token": tok})
        self.assertEqual(st, 200, body)
        pv = json.loads(body)
        self.assertEqual(c.get(pv["url"].split("?")[0])[0], 200)
        caps = pv["captions"]
        caps[0]["text"] = "EDITED caption"
        st, _, body = c.req("POST", f"/api/outputs/{oid}/render", json.dumps(
            {"start": a + 1, "end": b, "captions": caps, "hook": "New hook", "caption_style": "karaoke_fill",
             "cold_open": False, "title": "Edited title", "speed": 1.25, "fade_in": 0.5, "fade_out": 0.5,
             "volume_db": 2, "layout": "fit", "progress_bar": True}).encode(),
            {"Content-Type": "application/json", "X-CSRF-Token": tok})
        self.assertEqual(st, 200, body)
        rj = json.loads(body)["job"]
        for _ in range(240):
            j = json.loads(c.get(f"/api/jobs/{rj}")[2])
            if j["status"] in ("done", "failed"):
                break
            time.sleep(0.5)
        self.assertEqual(j["status"], "done", j)
        srt = c.get(f"/media/{oid}/srt")[2].decode()
        self.assertIn("EDITED", srt)
        st2 = json.loads(c.get(f"/api/outputs/{oid}")[2])
        self.assertEqual((st2["layout"], st2["edit"]["speed"], st2["progress_bar"]), ("fit", 1.25, True))
        self.assertAlmostEqual(st2["duration"], (b - a - 1) / 1.25, delta=0.6)
        st, h, _ = c.get(f"/media/{oid}/mp4?download=1")
        self.assertEqual(st, 200)
        self.assertIn("attachment", h.get("content-disposition", ""))
        # bad trim rejected
        st, _, _ = c.req("POST", f"/api/outputs/{oid}/render", json.dumps({"start": 0, "end": 9999}).encode(),
                         {"Content-Type": "application/json", "X-CSRF-Token": tok})
        self.assertEqual(st, 400)
        type(self).shared = {"c": c, "oid": oid, "jid": jid, "tok": tok, "outs": [o["id"] for o in outs]}
        # IDOR: another user sees nothing
        other = self._user("intruder@example.com")
        for path in (f"/media/{oid}/mp4", f"/api/outputs/{oid}", f"/app/jobs/{jid}", f"/app/review/{oid}", f"/api/jobs/{jid}"):
            self.assertEqual(other.get(path)[0], 404, path)
        # anonymous -> login redirect
        self.assertEqual(Client(self.base).get(f"/media/{oid}/mp4")[0], 303)


    def test_07_select_zip_publish_take_translate(self):
        import zipfile
        import io
        sh = getattr(type(self), "shared", None)
        if not sh:
            self.skipTest("needs test_06")
        c, oid, jid, tok = sh["c"], sh["oid"], sh["jid"], sh["tok"]
        hdr = {"Content-Type": "application/json", "X-CSRF-Token": tok}
        # zip of selected clips
        st, h, body = c.get("/media/zip?ids=" + ",".join(sh["outs"][:2]))
        self.assertEqual(st, 200)
        self.assertEqual(len(zipfile.ZipFile(io.BytesIO(body)).namelist()), 2)
        self.assertEqual(self._user("zipthief@example.com").get(f"/media/zip?ids={oid}")[0], 404)
        # publish: needs connected account + consent
        st, _, body = c.req("POST", "/api/publish", json.dumps({"output_ids": [oid], "targets": [{"account": "nope"}],
                                                                 "consent": True}).encode(), hdr)
        self.assertEqual(st, 400)
        self.assertIn(b"Connect", body)
        from viral_yt.web import publishing

        class FakeYT:
            label = "YouTube"
            calls = []

            def configured(self):
                return True

            def refresh(self, t):
                return t

            def publish(self, t, account_id, video, meta):
                FakeYT.calls.append((account_id, video.name, meta["privacy"], meta["title"], meta["description"]))
                return {"remote_id": "V1", "url": "https://www.youtube.com/shorts/V1", "status": meta["privacy"]}
        publishing._registry["youtube"] = FakeYT
        uid = self._uid("creator@example.com")
        acc_id = publishing.save_account(self.app.state.db, uid, "youtube",
                                         {"account_id": "UCtest", "handle": "@me", "token": {"access_token": "x"}})
        body = {"output_ids": [oid], "targets": [{"account": acc_id, "privacy": "unlisted"}], "caption": "hi"}
        st, _, r = c.req("POST", "/api/publish", json.dumps(body).encode(), hdr)
        self.assertEqual(st, 400)              # no consent
        body["consent"] = True
        st, _, r = c.req("POST", "/api/publish", json.dumps(body).encode(), hdr)
        self.assertEqual(st, 200, r)
        self._wait(c, json.loads(r)["job"])
        self.assertEqual(FakeYT.calls[-1][0], "UCtest")
        page_html = c.get(f"/app/jobs/{jid}")[2]
        self.assertIn(b"youtube.com/shorts/V1", page_html)
        self.assertIn(b"Virality", page_html)
        self.assertIn(b"act-pub", page_html)                          # Publish button on every clip
        # no caption typed -> the clip's AI social copy (with hashtags) is used; a typed title wins
        body2 = {"output_ids": [oid], "targets": [{"account": acc_id, "privacy": "unlisted"}], "consent": True,
                 "title": "My own title"}
        st, _, r = c.req("POST", "/api/publish", json.dumps(body2).encode(), hdr)
        self._wait(c, json.loads(r)["job"])
        self.assertEqual(FakeYT.calls[-1][3], "My own title")
        self.assertIn("#", FakeYT.calls[-1][4])
        self.assertIn("#shorts", FakeYT.calls[-1][4])
        # scheduling: rejected in the past, queued for later, shown on the calendar, cancellable once, owner only
        st, _, r = c.req("POST", "/api/publish", json.dumps({**body2, "schedule_at": time.time() - 10}).encode(), hdr)
        self.assertEqual(st, 400)
        n_calls = len(FakeYT.calls)
        when = time.time() + 3600
        st, _, r = c.req("POST", "/api/publish", json.dumps({**body2, "schedule_at": when}).encode(), hdr)
        self.assertEqual(st, 200, r)
        sj = json.loads(r)
        self.assertTrue(sj["scheduled"])
        row = self.app.state.db.one("SELECT status, not_before FROM jobs WHERE id=?", (sj["job"],))
        self.assertEqual((row["status"], round(row["not_before"])), ("queued", round(when)))
        time.sleep(1.5)
        self.assertEqual(len(FakeYT.calls), n_calls)                  # not posted early
        cal = c.get("/app/calendar")[2].decode()
        self.assertIn(sj["job"], cal)
        other = self._user("calthief@example.com")
        ot = re.search(rb'name="csrf" value="([^"]+)"', other.get("/app")[2]).group(1).decode()
        st, _, _ = other.req("POST", f"/api/calendar/{sj['job']}/cancel", b"{}", {"Content-Type": "application/json", "X-CSRF-Token": ot})
        self.assertEqual(st, 409)
        self.assertNotIn(sj["job"], other.get("/app/calendar")[2].decode())
        st, _, _ = c.req("POST", f"/api/calendar/{sj['job']}/cancel", b"{}", hdr)
        self.assertEqual(st, 200)
        st, _, _ = c.req("POST", f"/api/calendar/{sj['job']}/cancel", b"{}", hdr)
        self.assertEqual(st, 409)
        self.assertEqual(self.app.state.db.one("SELECT status FROM jobs WHERE id=?", (sj["job"],))["status"], "cancelled")
        # public posting is gated on originality
        from viral_yt.common import ROOT
        cfgp = ROOT / "config" / "settings.json"
        old = cfgp.read_text()
        try:
            d = json.loads(old); d["publishing"]["min_originality_score"] = 99; cfgp.write_text(json.dumps(d))
            body["targets"][0]["privacy"] = "public"
            st, _, r = c.req("POST", "/api/publish", json.dumps(body).encode(), hdr)
            self.assertEqual(st, 400)
            self.assertIn(b"originality", r)
        finally:
            cfgp.write_text(old)
        # other users can't post with this account
        thief = self._user("acctthief@example.com")
        self._activate(self._uid("acctthief@example.com"))
        tt = re.search(rb'name="csrf" value="([^"]+)"', thief.get("/app")[2]).group(1).decode()
        st, _, _ = thief.req("POST", "/api/publish", json.dumps({**body, "targets": [{"account": acc_id, "privacy": "private"}]}).encode(),
                             {"Content-Type": "application/json", "X-CSRF-Token": tt})
        self.assertEqual(st, 400)
        # make original + translate (flite voice + fake translator so the test is offline)
        if b" flite " not in subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True).stdout:
            return
        from viral_yt import transform
        real_speak = transform.speak
        transform.speak = lambda text, out, lang, engine=None: real_speak(text, out, lang, "flite")
        real_tr = transform.translate
        transform.translate = lambda texts, lang, translator=None: [("ES " + t) if t else t for t in texts]
        try:
            st, _, r = c.req("POST", f"/api/outputs/{oid}/do/take", json.dumps({"take": "My honest view on this moment."}).encode(), hdr)
            self.assertEqual(st, 200, r)
            self._wait(c, json.loads(r)["job"])
            self.assertEqual(c.get(f"/media/{oid}_take/mp4")[0], 200)
            st, _, r = c.req("POST", f"/api/outputs/{oid}/do/translate", json.dumps({"lang": "es"}).encode(), hdr)
            self.assertEqual(st, 200, r)
            self._wait(c, json.loads(r)["job"])
            self.assertIn("\nES\n", c.get(f"/media/{oid}_es/srt")[2].decode())   # word-level SRT
            st, _, _ = c.req("POST", f"/api/outputs/{oid}/do/translate", json.dumps({"lang": "xx"}).encode(), hdr)
            self.assertEqual(st, 400)
        finally:
            transform.speak, transform.translate = real_speak, real_tr

    def _wait(self, c, job):
        for _ in range(240):
            j = json.loads(c.get(f"/api/jobs/{job}")[2])
            if j["status"] in ("done", "failed"):
                break
            time.sleep(0.5)
        self.assertEqual(j["status"], "done", j)


if __name__ == "__main__":
    unittest.main()
