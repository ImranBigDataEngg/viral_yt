"""download_youtube keeps going when YouTube rate-limits subtitles (HTTP 429)."""
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ.setdefault("TRENDTIDE_ROOT", tempfile.mkdtemp())

from viral_yt import ingest  # noqa: E402


class DownloadError(Exception):
    pass


def fake_ytdlp(sub_fails=True, video_fails=None):
    calls = []

    class YDL:
        def __init__(self, opts):
            self.o = opts

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def download(self, urls):
            calls.append(self.o["writesubtitles"])
            if self.o["writesubtitles"] and sub_fails:
                raise DownloadError("ERROR: Unable to download video subtitles for 'en': HTTP Error 429: Too Many Requests")
            if video_fails:
                raise DownloadError(video_fails)
            Path(self.o["outtmpl"].replace("%(ext)s", "mp4")).write_bytes(b"x")

    mod = types.ModuleType("yt_dlp")
    mod.YoutubeDL = YDL
    utils = types.ModuleType("yt_dlp.utils")
    utils.DownloadError = DownloadError
    mod.utils = utils
    return {"yt_dlp": mod, "yt_dlp.utils": utils}, calls


class YtdlpFallback(unittest.TestCase):
    def test_subtitle_429_retries_without_subs(self):
        mods, calls = fake_ytdlp()
        with mock.patch.dict(sys.modules, mods):
            video, vtt = ingest.download_youtube("abc", Path(tempfile.mkdtemp()), 3600)
        self.assertEqual(calls, [True, False])
        self.assertTrue(video.is_file())
        self.assertIsNone(vtt)

    def test_only_original_english_tracks_requested(self):
        mods, _ = fake_ytdlp(sub_fails=False)
        seen = {}
        orig = mods["yt_dlp"].YoutubeDL.__init__

        def spy(self, o):
            seen.update(o)
            orig(self, o)
        mods["yt_dlp"].YoutubeDL.__init__ = spy
        with mock.patch.dict(sys.modules, mods):
            ingest.download_youtube("abc", Path(tempfile.mkdtemp()), 3600)
        self.assertEqual(seen["subtitleslangs"], ["en", "en-orig"])

    def test_video_429_is_a_friendly_error(self):
        mods, _ = fake_ytdlp(sub_fails=False, video_fails="ERROR: HTTP Error 429: Too Many Requests")
        with mock.patch.dict(sys.modules, mods):
            with self.assertRaisesRegex(ingest.IngestError, "rate-limiting"):
                ingest.download_youtube("abc", Path(tempfile.mkdtemp()), 3600)


if __name__ == "__main__":
    unittest.main()
