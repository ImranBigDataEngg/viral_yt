import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from viral_yt.common import safe_name
from viral_yt.discovery import rank_video
from viral_yt.rights import RightsError, validate_asset
from viral_yt.transcripts import parse_srt, seconds_to_srt, srt_to_seconds, select_highlight


class TestCore(unittest.TestCase):
    def test_safe_name(self):
        self.assertEqual(safe_name("GTA 6: what's new?"), "GTA_6_what_s_new")

    def test_srt(self):
        self.assertEqual(seconds_to_srt(61.234), "00:01:01,234")
        self.assertEqual(srt_to_seconds("00:01:01,234"), 61.234)
        cues = parse_srt("1\n00:00:00,000 --> 00:00:10,000\nWhy does this matter?\n\n2\n00:00:10,000 --> 00:00:25,000\nBecause it changes everything!\n")
        self.assertEqual(len(cues), 2)
        selected = select_highlight(cues)
        self.assertGreaterEqual(selected[1] - selected[0], 6)

    def test_scoring(self):
        item = {"id": "x", "snippet": {"publishedAt": "2026-11-18T00:00:00Z", "title": "Test", "channelTitle": "Owner"}, "statistics": {"viewCount": "12000", "likeCount": "500", "commentCount": "40"}}
        score = rank_video(item, now=datetime(2026, 11, 19, tzinfo=timezone.utc))
        self.assertGreater(score["score"], 0)
        self.assertEqual(score["views"], 12000)
        self.assertEqual(score["video_id"], "x")

    def test_rights_rejects_unapproved(self):
        with tempfile.NamedTemporaryFile() as f:
            asset = dict(asset_id="id", source_file=f.name, rights_basis="original", rights_evidence="Owned recording proof", approved="false", clip_start="0", clip_end="20")
            with self.assertRaises(RightsError):
                validate_asset(asset)

    def test_rights_rejects_unlicensed(self):
        with tempfile.NamedTemporaryFile() as f:
            asset = dict(asset_id="id", source_file=f.name, rights_basis="unknown", rights_evidence="This is not proof", approved="true", clip_start="0", clip_end="20")
            with self.assertRaises(RightsError):
                validate_asset(asset)

    def test_rights_requires_commentary_for_third_party(self):
        with tempfile.NamedTemporaryFile() as f:
            asset = dict(asset_id="id", source_file=f.name, rights_basis="licensed", rights_evidence="Contract licenses source", approved="true", clip_start="0", clip_end="20")
            with self.assertRaisesRegex(RightsError, "commentary_audio"):
                validate_asset(asset)

    def test_publish_requires_separate_approval(self):
        with tempfile.NamedTemporaryFile() as f:
            asset = dict(asset_id="id", source_file=f.name, rights_basis="original", rights_evidence="Original self recording", approved="true", publish_approved="false", clip_start="0", clip_end="20")
            with self.assertRaisesRegex(RightsError, "publish_approved"):
                validate_asset(asset, require_publish=True)


if __name__ == "__main__":
    unittest.main()
