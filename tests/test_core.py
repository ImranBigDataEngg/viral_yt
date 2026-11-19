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


class TestClipEngine(unittest.TestCase):
    def _sents(self, n=30):
        from viral_yt.media_prep import sentences
        words, t = [], 0.0
        for i in range(n):
            line = ("Why does nobody talk about this?" if i % 7 == 0 else f"Point {i} adds real detail here today.").split()
            for k, w in enumerate(line):
                words.append((t, t + 0.35, w))
                t += 0.4
            t += 0.5
        return sentences(words)

    def test_sentences_from_words(self):
        sents = self._sents(5)
        self.assertEqual(len(sents), 5)
        self.assertTrue(sents[0][2].endswith("?"))

    def test_align_cut_prefers_silence(self):
        from viral_yt.media_prep import align_cut
        sil = [(9.6, 10.4)]
        self.assertTrue(9.6 < align_cut(10.0, sil, "start") < 10.4)
        self.assertAlmostEqual(align_cut(20.0, sil, "start"), 19.75)
        self.assertAlmostEqual(align_cut(20.0, sil, "end"), 20.35)

    def test_hybrid_llm_selection_with_retry(self):
        from viral_yt.highlights import select_clips
        sents = self._sents(40)
        calls = []

        def fake(prompt):
            calls.append(prompt)
            if len(calls) == 1:
                return "sorry, not json"
            import re
            ids = [int(x) for x in re.findall(r"<candidate id=(\d+)>", prompt)]
            return json.dumps({"clips": [{"id": i, "start": 0, "end": 10**6, "score": 90 - i, "title": f"T{i}",
                                          "hook": "x", "quote": None, "reason": "r"} for i in ids]})
        clips, stats = select_clips(sents, 3, 15, 40, use_llm=True, completer=fake)
        self.assertEqual(stats["mode"], "hybrid-llm")
        self.assertEqual(len(calls), 2)                   # retried once with the validation error
        self.assertIn("previous reply was invalid", calls[1])
        self.assertGreaterEqual(len(clips), 2)
        for a, b in zip(clips, clips[1:]):
            self.assertLessEqual(a["end"], b["start"])
        self.assertLess(stats["llm_tokens_est"], 4000)

    def test_llm_failure_falls_back_to_heuristic(self):
        from viral_yt.highlights import select_clips
        clips, stats = select_clips(self._sents(40), 3, 15, 40, use_llm=True, completer=lambda p: "nope")
        self.assertEqual(stats["mode"], "heuristic")
        self.assertIn("llm_error", stats)
        self.assertTrue(clips)

    def test_reframe_plan(self):
        from viral_yt.reframe import SAMPLE_FPS, plan_segments
        aw = 640
        left = [[(150, 180, 120)]] * (4 * SAMPLE_FPS)
        right = [[(500, 180, 120)]] * (4 * SAMPLE_FPS)
        two = [[(100, 180, 90), (560, 180, 90)]] * (4 * SAMPLE_FPS)
        none = [[]] * (4 * SAMPLE_FPS)
        segs = plan_segments(16.0, [4.0, 8.0, 12.0, 12.3], left + right + two + none, 1920, 1080, aw)
        self.assertEqual([s["mode"] for s in segs], ["crop", "crop", "fit"])   # two-shot + no-face merge into fit
        self.assertLess(segs[0]["x"], segs[1]["x"])
        self.assertTrue(all(0 <= s["x"] <= 1920 - s["cw"] for s in segs))
        self.assertEqual(segs[-1]["end"], 16.0)

    def test_caption_styles(self):
        words = [(0.0, 0.4, "one"), (0.5, 0.9, "two"), (1.0, 1.3, "three.")]
        for style in ("bold_pop", "karaoke_fill", "boxed", "clean_lower"):
            body = build_ass(words, Path(_TMP) / f"{style}.ass", style=style).read_text()
            self.assertIn(",Cap,", body)
        self.assertIn("\\kf", (Path(_TMP) / "karaoke_fill.ass").read_text())


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
