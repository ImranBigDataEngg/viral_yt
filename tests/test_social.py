"""Platform adapters against scripted fake HTTP (no network): request sequence, auth headers, privacy rules."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from viral_yt.social.http import Http, PlatformError  # noqa: E402
from viral_yt.social.platforms import Instagram, TikTok, YouTube, pkce_challenge  # noqa: E402
from viral_yt.social.tokens import TokenVault  # noqa: E402


class FakeHttp(Http):
    def __init__(self, script):
        self.script, self.calls, self.last_headers = list(script), [], {}

    def call(self, method, url, **kw):
        self.calls.append((method, url.split("?")[0], kw))
        expect, resp, headers = self.script.pop(0)
        assert expect in f"{method} {url}", f"expected {expect!r}, got {method} {url}"
        self.last_headers = headers or {}
        if isinstance(resp, Exception):
            raise resp
        return resp


class TestSocial(unittest.TestCase):
    def setUp(self):
        self.video = Path(tempfile.mkstemp(suffix=".mp4")[1])
        self.video.write_bytes(b"\x00" * 2048)
        os.environ.update(IG_APP_ID="1", IG_APP_SECRET="s", TIKTOK_CLIENT_KEY="k", TIKTOK_CLIENT_SECRET="s",
                          YT_CLIENT_ID="c", YT_CLIENT_SECRET="s")

    def tearDown(self):
        self.video.unlink(missing_ok=True)

    def test_vault_roundtrip_and_tamper(self):
        v = TokenVault("x" * 48)
        blob = v.seal({"access_token": "abc"})
        self.assertNotIn("abc", blob)
        self.assertEqual(v.open(blob)["access_token"], "abc")
        with self.assertRaises(ValueError):
            TokenVault("y" * 48).open(blob)

    def test_pkce(self):
        self.assertEqual(pkce_challenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"), "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM")

    def test_youtube_resumable_upload(self):
        http = FakeHttp([("POST https://www.googleapis.com/upload/youtube/v3/videos", {}, {"location": "https://up/session1"}),
                         ("PUT https://up/session1", {"id": "VID123", "status": {"privacyStatus": "unlisted"}}, None)])
        r = YouTube(http).publish({"access_token": "t"}, "UC1", self.video,
                                  {"title": "T", "privacy": "unlisted", "synthetic": True})
        self.assertEqual(r["url"], "https://www.youtube.com/shorts/VID123")
        body = http.calls[0][2]["json_body"]
        self.assertTrue(body["status"]["containsSyntheticMedia"])
        self.assertEqual(http.calls[1][2]["headers"]["Authorization"], "Bearer t")

    def test_instagram_reel_flow(self):
        http = FakeHttp([("POST https://graph.instagram.com/v26.0/IG1/media", {"id": "C1"}, None),
                         ("POST https://rupload.facebook.com/ig-api-upload/v26.0/C1", {"success": True}, None),
                         ("GET https://graph.instagram.com/v26.0/C1", {"status_code": "IN_PROGRESS"}, None),
                         ("GET https://graph.instagram.com/v26.0/C1", {"status_code": "FINISHED"}, None),
                         ("POST https://graph.instagram.com/v26.0/IG1/media_publish", {"id": "M1"}, None),
                         ("GET https://graph.instagram.com/v26.0/M1", {"permalink": "https://instagram.com/reel/x"}, None)])
        r = Instagram(http).publish({"access_token": "t"}, "IG1", self.video, {"caption": "hi", "duration": 30}, poll_s=0)
        self.assertEqual(r["url"], "https://instagram.com/reel/x")
        self.assertEqual(http.calls[0][2]["params"]["media_type"], "REELS")
        self.assertEqual(http.calls[1][2]["headers"]["file_size"], "2048")

    def test_tiktok_direct_post_and_privacy_guard(self):
        info = {"data": {"privacy_level_options": ["SELF_ONLY"], "max_video_post_duration_sec": 600, "creator_nickname": "n"},
                "error": {"code": "ok"}}
        http = FakeHttp([("POST https://open.tiktokapis.com/v2/post/publish/creator_info/query/", info, None)])
        with self.assertRaisesRegex(PlatformError, "not available"):
            TikTok(http).publish({"access_token": "t"}, "o", self.video, {"tiktok_privacy": "PUBLIC_TO_EVERYONE", "duration": 20})
        http = FakeHttp([("POST https://open.tiktokapis.com/v2/post/publish/creator_info/query/", info, None),
                         ("POST https://open.tiktokapis.com/v2/post/publish/video/init/",
                          {"data": {"publish_id": "P1", "upload_url": "https://upload/x"}, "error": {"code": "ok"}}, None),
                         ("PUT https://upload/x", {}, None),
                         ("POST https://open.tiktokapis.com/v2/post/publish/status/fetch/", {"data": {"status": "PUBLISH_COMPLETE"}}, None)])
        r = TikTok(http).publish({"access_token": "t"}, "o", self.video, {"tiktok_privacy": "SELF_ONLY", "duration": 20, "caption": "c"}, poll_s=0)
        self.assertEqual(r["remote_id"], "P1")
        init = http.calls[1][2]["json_body"]
        self.assertEqual(init["source_info"], {"source": "FILE_UPLOAD", "video_size": 2048, "chunk_size": 2048, "total_chunk_count": 1})
        self.assertEqual(http.calls[2][2]["headers"]["Content-Range"], "bytes 0-2047/2048")


if __name__ == "__main__":
    unittest.main()
