"""VoiceStudio HTTP engine, tested against a tiny fake of its OpenAI-compatible API."""
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

_TMP = tempfile.mkdtemp(prefix="trendtide_vs_")
os.environ.setdefault("TRENDTIDE_ROOT", _TMP)
REPO = Path(__file__).resolve().parents[1]
if not (Path(os.environ["TRENDTIDE_ROOT"]) / "config").exists():
    shutil.copytree(REPO / "config", Path(os.environ["TRENDTIDE_ROOT"]) / "config")
sys.path.insert(0, str(REPO / "src"))

from viral_yt import transform, tts, voicestudio  # noqa: E402


def _wav(seconds=0.5) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000)
        w.writeframes(b"\x00\x00" * int(24000 * seconds))
    return buf.getvalue()


class Fake(BaseHTTPRequestHandler):
    calls: list = []

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            return self._send(200, b'{"status":"ok"}', "application/json")
        if self.path == "/v1/audio/voices":
            return self._send(200, b'{"voices":[{"id":"default"},{"id":"my-voice"}]}', "application/json")
        self._send(404, b"{}", "application/json")

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        Fake.calls.append((self.path, self.headers.get("Authorization"), body))
        if self.path == "/v1/audio/speech":
            req = json.loads(body)
            if req.get("model") == "nc-model":
                return self._send(403, b'{"error":{"message":"licence not accepted"}}', "application/json")
            return self._send(200, _wav(), "audio/wav")
        if self.path == "/v1/audio/transcriptions":
            return self._send(200, b'{"text":"hello world"}', "application/json")
        self._send(404, b"{}", "application/json")


class VoiceStudioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.srv.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        Fake.calls.clear()
        self.out = Path(tempfile.mkdtemp())

    def env(self, **kv):
        base = {"VOICESTUDIO_URL": self.url, "VOICESTUDIO_API_KEY": ""}
        base.update(kv)
        return mock.patch.dict(os.environ, base)

    def test_disabled_by_default(self):
        with mock.patch.dict(os.environ, {"VOICESTUDIO_URL": ""}):
            self.assertFalse(voicestudio.enabled())

    def test_speak_writes_wav_and_sends_language(self):
        with self.env():
            self.assertTrue(voicestudio.health())
            p = voicestudio.speak("Hola mundo", self.out / "a.wav", lang="es")
        self.assertEqual(p.read_bytes()[:4], b"RIFF")
        req = json.loads(Fake.calls[0][2])
        self.assertEqual((req["language"], req["voice"], req["response_format"]), ("es", "default", "wav"))

    def test_key_sent_as_bearer_on_loopback(self):
        with self.env(VOICESTUDIO_API_KEY="k123"):
            voicestudio.speak("hi there", self.out / "b.wav")
        self.assertEqual(Fake.calls[0][1], "Bearer k123")

    def test_key_never_sent_over_plain_http_to_remote(self):
        with self.env(VOICESTUDIO_URL="http://203.0.113.9:3900", VOICESTUDIO_API_KEY="k123"):
            with self.assertRaises(voicestudio.VoiceStudioError):
                voicestudio.speak("hi", self.out / "c.wav")

    def test_licence_error_is_explained(self):
        with self.env(), mock.patch.object(voicestudio, "config",
                                           return_value={"url": self.url, "api_key": "", "model": "nc-model",
                                                         "voice": "default", "voices": {}}):
            with self.assertRaisesRegex(voicestudio.VoiceStudioError, "Model Manager"):
                voicestudio.speak("hi", self.out / "d.wav")

    def test_unreachable_is_clear(self):
        with self.env(VOICESTUDIO_URL="http://127.0.0.1:9"):
            self.assertFalse(voicestudio.health(timeout=1))
            with self.assertRaisesRegex(voicestudio.VoiceStudioError, "not reachable"):
                voicestudio.speak("hi", self.out / "e.wav")

    def test_tts_auto_prefers_voicestudio(self):
        with self.env():
            path, engine = tts.synthesize("Original script line.", self.out / "f.wav")
        self.assertEqual(engine, "voicestudio")

    def test_dub_languages_and_speak_route_through_service(self):
        with self.env():
            self.assertEqual(set(transform.available_languages()), set(transform.LANGS))
            transform.speak("Bonjour", self.out / "g.wav", "fr")
        self.assertEqual(json.loads(Fake.calls[0][2])["language"], "fr")

    def test_transcribe(self):
        wav = self.out / "in.wav"
        wav.write_bytes(_wav())
        with self.env():
            self.assertEqual(voicestudio.transcribe(wav), "hello world")


if __name__ == "__main__":
    unittest.main()
