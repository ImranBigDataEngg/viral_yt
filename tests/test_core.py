import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="trendtide_test_")
os.environ["TRENDTIDE_ROOT"] = _TMP  # isolate state.db / output from the real workspace
REPO = Path(__file__).resolve().parents[1]
shutil.copytree(REPO / "config", Path(_TMP) / "config")
sys.path.insert(0, str(REPO / "src"))

from viral_yt.captions import build_ass, group_words  # noqa: E402
from viral_yt.common import safe_name  # noqa: E402
from viral_yt.discovery import Api, discover, iso_duration, rank_video  # noqa: E402
from viral_yt.highlights import find_highlights  # noqa: E402
from viral_yt.originality import score as originality  # noqa: E402
from viral_yt.publisher import build_body  # noqa: E402
from viral_yt.rights import RightsError, parse_ranges, validate_asset  # noqa: E402
from viral_yt.scriptgen import check_ready, make_brief  # noqa: E402
from viral_yt.transcripts import parse_srt, seconds_to_srt, select_highlight, srt_to_seconds  # noqa: E402


def _video(vid, views, license_="youtube", duration="PT12M", channel="C1", hours_old=10):
    pub = datetime(2026, 11, 19, tzinfo=timezone.utc).timestamp() - hours_old * 3600
    return {"id": vid, "snippet": {"publishedAt": datetime.fromtimestamp(pub, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                   "title": f"GTA 6 trailer {vid}", "channelTitle": "Owner", "channelId": channel},
            "statistics": {"viewCount": str(views), "likeCount": "100", "commentCount": "10"},
            "contentDetails": {"duration": duration}, "status": {"license": license_}}


class FakeApi(Api):
    def __init__(self):
        super().__init__("k")

    def get(self, endpoint, params, attempts=3):
        self.calls[endpoint] = self.calls.get(endpoint, 0) + 1
        if endpoint == "search":
            if params.get("videoLicense") == "creativeCommon":
                return {"items": [{"id": {"videoId": "cc1"}}]}
            return {"items": [{"id": {"videoId": "a"}}, {"id": {"videoId": "b"}}]}
        if endpoint == "videos" and params.get("chart"):
            return {"items": [{"id": "a"}]}
        if endpoint == "videos":
            data = {"a": _video("a", 90000), "b": _video("b", 1000), "cc1": _video("cc1", 5000, "creativeCommon")}
            return {"items": [data[i] for i in params["id"].split(",") if i in data]}
        if endpoint == "channels":
            return {"items": [{"id": "C1", "statistics": {"subscriberCount": "1000"}}]}
        return {}


class TestCore(unittest.TestCase):
    def test_safe_name(self):
        self.assertEqual(safe_name("GTA 6: what's new?"), "GTA_6_what_s_new")

    def test_srt_roundtrip(self):
        self.assertEqual(seconds_to_srt(61.234), "00:01:01,234")
        self.assertEqual(srt_to_seconds("00:01:01,234"), 61.234)

    def test_iso_duration(self):
        self.assertEqual(iso_duration("PT1H2M3S"), 3723)
        self.assertEqual(iso_duration("PT45S"), 45)

    def test_scoring_and_cc_flag(self):
        r = rank_video(_video("x", 12000, "creativeCommon"), now=datetime(2026, 11, 19, tzinfo=timezone.utc), channel_subs=500)
        self.assertGreater(r["score"], 0)
        self.assertTrue(r["clip_candidate"])
        self.assertEqual(r["outlier_ratio"], 24.0)
        self.assertFalse(rank_video(_video("y", 1, "youtube"))["clip_candidate"])

    def test_discover_with_fake_api(self):
        topics = Path(_TMP) / "t.json"
        topics.write_text(json.dumps({"Gaming": ["GTA 6"]}))
        rows = discover(top=10, categories_file=topics, api=FakeApi(), write=True)
        ids = [r["video_id"] for r in rows]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertIn("cc1", ids)
        self.assertTrue(all(r["is_new"] for r in rows))
        again = discover(top=10, categories_file=topics, api=FakeApi(), write=True)
        self.assertFalse(any(r["is_new"] for r in again))

    def test_highlights_non_overlapping_and_complete(self):
        cues, t = [], 0.0
        for i in range(40):
            text = "Why does this matter?" if i % 8 == 0 else f"Point number {i} explains the idea clearly."
            cues.append((t, t + 3.0, text))
            t += 3.2
        hl = find_highlights(cues, 20, 58, 3)
        self.assertEqual(len(hl), 3)
        for a, b in zip(hl, hl[1:]):
            self.assertLessEqual(a["end"], b["start"])
        self.assertTrue(all(20 <= h["duration"] <= 58 for h in hl))
        start, end, _ = select_highlight(parse_srt("1\n00:00:00,000 --> 00:00:10,000\nWhy does this matter?\n\n"
                                                   "2\n00:00:10,000 --> 00:00:25,000\nBecause it changes everything!\n"))
        self.assertGreaterEqual(end - start, 6)

    def test_captions_ass(self):
        words = [(0.0, 0.3, "Hello"), (0.3, 0.6, "world."), (0.7, 1.0, "Next"), (1.0, 1.4, "line")]
        self.assertEqual(len(group_words(words, 3)), 2)
        out = build_ass(words, Path(_TMP) / "x.ass", hook_text="hook", credit="c", total=2)
        body = out.read_text()
        self.assertIn("Style: Hook", body)
        self.assertEqual(body.count(",Cap,"), 4)

    def test_rights_rules(self):
        with tempfile.NamedTemporaryFile() as f:
            base = dict(asset_id="id", source_file=f.name, rights_basis="original", rights_evidence="Owned recording proof",
                        approved="true", publish_approved="false", clip_start="0", clip_end="20")
            with self.assertRaises(RightsError):
                validate_asset({**base, "approved": "false"})
            with self.assertRaises(RightsError):
                validate_asset({**base, "rights_basis": "found_it_on_youtube"})
            with self.assertRaisesRegex(RightsError, "commentary_audio"):
                validate_asset({**base, "rights_basis": "cc_by"})
            with self.assertRaisesRegex(RightsError, "publish_approved"):
                validate_asset(base, require_publish=True)
            with self.assertRaisesRegex(RightsError, "transcript_file"):
                validate_asset({**base, "clip_ranges": "auto"})
            self.assertEqual(parse_ranges("10-40; 60-90"), [(10.0, 40.0), (60.0, 90.0)])

    def test_brief_blocks_until_verified(self):
        brief = make_brief({"category": "Gaming", "video_id": "v", "title": "GTA 6 leak explained", "search_term": "GTA 6",
                            "angle_hint": "Myth vs Confirmed"})
        problems = check_ready(brief)
        self.assertTrue(any("VERIFY" in p for p in problems))
        self.assertIn("Myth", brief["angle"])

    def test_originality_gate(self):
        repost, _ = originality({"kind": "clip", "rights_basis": "cc_by", "source_audio": "full"})
        good, _ = originality({"kind": "explainer", "voice": "own", "visuals_kind": "own", "captions_included": True,
                               "hook_text": "x", "layout": "blurfit", "title": "A"})
        dup, _ = originality({"kind": "explainer", "voice": "own", "visuals_kind": "own", "title": "Same title"}, ["same title"])
        self.assertLess(repost, 70)
        self.assertGreaterEqual(good, 70)
        self.assertLess(dup, good)

    def test_upload_body(self):
        body = build_body({"title": "T", "description": "D", "tags": ["a"], "kind": "clip", "rights_basis": "cc_by",
                           "source_owner": "Creator", "contains_realistic_synthetic_media": True}, "private", None)
        self.assertEqual(body["status"]["privacyStatus"], "private")
        self.assertTrue(body["status"]["containsSyntheticMedia"])
        self.assertIn("Creator", body["snippet"]["description"])
        sched = build_body({"title": "T"}, "public", "2026-12-01T15:00:00Z")
        self.assertEqual(sched["status"]["privacyStatus"], "private")
        self.assertIn("publishAt", sched["status"])


@unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg not installed")
class TestRenderSmoke(unittest.TestCase):
    def test_render_clip(self):
        import csv
        import subprocess
        from viral_yt.clipper import render_all
        root = Path(_TMP)
        (root / "media/authorized").mkdir(parents=True, exist_ok=True)
        src = root / "media/authorized/s.mp4"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:d=10",
                        "-f", "lavfi", "-i", "sine=f=330:d=10", "-c:v", "libx264", "-preset", "ultrafast",
                        "-c:a", "aac", "-shortest", str(src)], check=True)
        with (REPO / "examples/assets_template.csv").open(encoding="utf-8-sig") as t:
            fields = next(csv.reader(t))
        with (root / "media/authorized/assets.csv").open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerow(dict(asset_id="s", source_file="media/authorized/s.mp4", owner="me", rights_basis="original",
                            rights_evidence="made in test", clip_start="1", clip_end="8", hook_text="Test", approved="true"))
        out = render_all("s", preset="ultrafast")[0]
        self.assertTrue(out.is_file())
        meta = json.loads(out.with_suffix(".json").read_text())
        self.assertEqual(meta["duration_seconds"], 7.0)


if __name__ == "__main__":
    unittest.main()
