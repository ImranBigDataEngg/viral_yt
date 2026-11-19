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

    def test_05_ssrf_blocked(self):
        c = self._user("ssrf@example.com")
        self._activate(self._uid("ssrf@example.com"))
        for url in ("http://127.0.0.1/x.mp4", "https://127.0.0.1/x.mp4", "https://user:pw@example.com/x.mp4",
                    "https://localhost/x.mp4", "https://[::1]/x.mp4", "https://169.254.169.254/latest"):
            st, h, _ = c.multipart("/app/jobs", [("csrf", c.csrf("/app")), ("url", url), ("attest", "on"),
                                                 ("rights_basis", "original"), ("rights_evidence", "my own video file")], {})
            self.assertEqual(st, 303)
            self.assertIn("/app?notice=", h.get("location", ""), url)

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
             "cold_open": False, "title": "Edited title"}).encode(), {"Content-Type": "application/json", "X-CSRF-Token": tok})
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
        st, h, _ = c.get(f"/media/{oid}/mp4?download=1")
        self.assertEqual(st, 200)
        self.assertIn("attachment", h.get("content-disposition", ""))
        # bad trim rejected
        st, _, _ = c.req("POST", f"/api/outputs/{oid}/render", json.dumps({"start": 0, "end": 9999}).encode(),
                         {"Content-Type": "application/json", "X-CSRF-Token": tok})
        self.assertEqual(st, 400)
        # IDOR: another user sees nothing
        other = self._user("intruder@example.com")
        for path in (f"/media/{oid}/mp4", f"/api/outputs/{oid}", f"/app/jobs/{jid}", f"/app/review/{oid}", f"/api/jobs/{jid}"):
            self.assertEqual(other.get(path)[0], 404, path)
        # anonymous -> login redirect
        self.assertEqual(Client(self.base).get(f"/media/{oid}/mp4")[0], 303)


if __name__ == "__main__":
    unittest.main()
