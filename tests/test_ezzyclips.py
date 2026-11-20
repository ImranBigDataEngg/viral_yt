import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from viral_yt.channel import keywords_string, planned_changes
from viral_yt.clipper import chunk_cues
from viral_yt.rights import RightsError, check_originality
from viral_yt.series import SERIES, build_description, build_hashtags, build_title, script_scaffold


class TestEzzyClips(unittest.TestCase):
    def test_originality_gate_blocks_thin_commentary(self):
        asset = {"rights_basis": "licensed"}
        with self.assertRaisesRegex(RightsError, "needs at least 50%"):
            check_originality(asset, clip_seconds=30, commentary_seconds=10)
        check_originality(asset, clip_seconds=30, commentary_seconds=16)

    def test_originality_gate_skips_own_footage(self):
        check_originality({"rights_basis": "original"}, clip_seconds=30, commentary_seconds=0)

    def test_title_and_warning(self):
        title, warnings = build_title("missed_this", "Parking")
        self.assertEqual(title, "You Missed This in the Viral Parking Clip")
        self.assertEqual(warnings, [])
        _, warnings = build_title("missed_this", "Extremely Long Subject Name That Goes On")
        self.assertTrue(warnings)

    def test_hashtags_capped_and_topic_first(self):
        tags = build_hashtags("real_or_fake", "shark, ocean beach viral")
        self.assertEqual(tags[0], "#shark")
        self.assertLessEqual(len(tags), 5)
        self.assertEqual(len(tags), len(set(tags)))

    def test_description_has_credit(self):
        desc = build_description({"series": "meme_origins", "description": "Where it began.", "owner": "@creator", "source_video_url": "https://example.com/v"})
        self.assertIn("Original clip: @creator - https://example.com/v", desc)
        self.assertIn("#memeorigins", desc)

    def test_script_scaffold(self):
        out = script_scaffold({"asset_id": "a1", "series": "how_filmed", "subject": "Mirror Shot"})
        self.assertEqual(len(out["beats"]), 4)
        self.assertIn(out["beats"][0]["text"], SERIES["how_filmed"]["hooks"])

    def test_caption_chunks(self):
        chunks = chunk_cues([(0.0, 3.0, "one two three four five six")])
        self.assertEqual([c[2] for c in chunks], ["one two three", "four five six"])
        self.assertAlmostEqual(chunks[-1][1], 3.0)

    def test_channel_plan(self):
        kw = keywords_string()
        self.assertLessEqual(len(kw), 500)
        self.assertIn('"meme origins"', kw)
        plan = planned_changes()
        self.assertEqual(len(plan["playlists"]), 5)
        self.assertLessEqual(len(plan["brandingSettings"]["channel"]["description"]), 1000)


if __name__ == "__main__":
    unittest.main()
