"""End-to-end offline demo — no API keys, no third-party media.

1. Generates an ORIGINAL 16:9 'long-form' video with spoken narration (FFmpeg flite TTS) + exact SRT.
2. Registers it in the rights ledger with clip_ranges=auto -> finds highlights -> renders vertical clips.
3. Builds an original explainer from a brief -> TTS voice-over, animated captions, motion background.
4. Writes the download manifest CSV + HTML gallery and shows the (dry-run) YouTube upload body.
"""
from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from viral_yt.common import ROOT  # noqa: E402
from viral_yt.ffmpeg_tools import check_ffmpeg, probe_duration  # noqa: E402
from viral_yt.transcripts import write_srt  # noqa: E402

SENTENCES = [
    "Welcome back to the TrendTide demo channel.",
    "Why does one video explode while a thousand others vanish?",
    "The first reason is speed. Views per hour beat total views every time.",
    "The second reason is the hook. Viewers decide in under two seconds.",
    "Here's the part nobody tells you. Retention matters more than clicks.",
    "So what should you actually do?",
    "Open with the payoff, cut every pause, and caption every word.",
    "That simple checklist doubled our average view duration in testing.",
    "But wait, there is a catch. Reposting other people's clips gets you demonetized.",
    "The fix is original commentary, your own footage, or written permission.",
    "Do that consistently and the algorithm has something real to reward.",
    "Now, a quick story about our worst upload.",
    "We spent ten hours on a video and it got forty views.",
    "Why? The first three seconds were a logo animation.",
    "Nobody waits for a logo. They swipe.",
    "The next week we opened with the result instead.",
    "Same topic, same length, and it reached twelve thousand people.",
    "The lesson is simple. Earn the next second, every second.",
    "Thanks for watching. Follow for the story behind every trend.",
]


def tts(text: str, out: Path) -> None:
    txt = out.with_suffix(".txt")
    txt.write_text(text, encoding="utf-8")
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"flite=textfile='{txt.as_posix()}':voice=slt", "-ar", "48000", "-ac", "1", str(out)], check=True)


def make_longform(folder: Path) -> tuple[Path, Path]:
    parts, cues, t = [], [], 0.0
    for i, s in enumerate(SENTENCES):
        wav = folder / f"_seg{i:02}.wav"
        tts(s, wav)
        d = probe_duration(wav)
        cues.append((t, t + d, s))
        t += d + 0.35
        parts.append(wav)
    lst = folder / "_segments.txt"
    gap = folder / "_gap.wav"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "anullsrc=r=48000:cl=mono", "-t", "0.35", str(gap)], check=True)
    lst.write_text("".join(f"file '{p.name}'\nfile '{gap.name}'\n" for p in parts), encoding="utf-8")
    voice = folder / "demo_longform_voice.wav"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst.name,
                    "-c", "copy", voice.name], check=True, cwd=folder)
    total = probe_duration(voice)
    video = folder / "demo_longform.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"testsrc2=size=1920x1080:rate=30:d={total}", "-i", str(voice), "-shortest",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", str(video)], check=True)
    srt = write_srt(cues, ROOT / "media" / "transcripts" / "demo_longform.srt")
    for p in folder.glob("_*"):
        p.unlink()
    return video, srt


def main() -> None:
    check_ffmpeg()
    folder = ROOT / "media" / "authorized"
    folder.mkdir(parents=True, exist_ok=True)
    video, srt = make_longform(folder)
    print(f"[1] original long-form: {video.name} ({probe_duration(video):.1f}s), transcript {srt.name}")

    ledger = folder / "assets.csv"
    template = ROOT / "examples" / "assets_template.csv"
    fields = next(csv.reader(template.open(encoding="utf-8-sig")))
    rows = [r for r in csv.DictReader(ledger.open(encoding="utf-8-sig"))] if ledger.exists() else []
    rows = [r for r in rows if r.get("asset_id") != "demo_longform"]
    rows.append(dict(asset_id="demo_longform", category="Demo", source_file="media/authorized/demo_longform.mp4",
                     owner="TrendTide (demo)", rights_basis="original", rights_evidence="Synthetic video generated locally with FFmpeg",
                     transcript_file="media/transcripts/demo_longform.srt", clip_ranges="auto", layout="blurfit",
                     hook_text="Why videos go viral", title="Why some videos go viral", tags="viral|youtube tips|shorts",
                     description="Demo clip cut from our own long-form video.", approved="true", publish_approved="false"))
    with ledger.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    from viral_yt.clipper import render_all
    clips = render_all("demo_longform", preset="veryfast")
    print(f"[2] clips: {[c.name for c in clips]}")

    from viral_yt.scriptgen import make_brief
    cand = {"category": "Gaming", "video_id": "DEMO123", "title": "GTA 6 trailer 3 breakdown", "search_term": "GTA 6",
            "angle_hint": "3 details you missed + why it matters", "url": "https://www.youtube.com/watch?v=DEMO123",
            "channel": "(research pointer only)", "views": 0}
    brief = make_brief(cand)
    brief["brief_id"] = "demo_explainer"
    brief["script"] = {
        "hook": "Three trailer details almost everyone missed.",
        "context": "This is a demo script, so treat every line as a placeholder for facts you verify yourself.",
        "beats": ["Detail one: look at the background, not the hero.",
                  "Detail two: count the seconds between cuts. Pacing tells you the tone.",
                  "Detail three: my take. The marketing is selling a place, not a plot."],
        "payoff": "So next time, pause on the wide shots. That is where the story hides.",
        "cta": "Follow TrendTide for the story behind every trend.",
    }
    brief.update(title="3 trailer details everyone missed (demo)", description="Original demo explainer. Sources: none (demo).",
                 visual_rights="generated motion background", approved=True)
    bf = ROOT / "output" / "briefs" / "demo_explainer.json"
    bf.parent.mkdir(parents=True, exist_ok=True)
    bf.write_text(json.dumps(brief, indent=2), encoding="utf-8")
    from viral_yt.composer import compose
    ex = compose(str(bf), preset="veryfast")
    print(f"[3] explainer: {ex.name} ({probe_duration(ex):.1f}s)")

    from viral_yt.manifest import build_manifest
    from viral_yt.publisher import upload
    man, items = build_manifest()
    print(f"[4] manifest: {man}  gallery: {man.with_name('index.html')}  items={len(items)}")
    dry = upload("demo_explainer")
    print("[5] dry-run upload body:", json.dumps(dry["body"], indent=1)[:600])
    for c in clips + [ex]:
        meta = json.loads(c.with_suffix(".json").read_text())
        print(f"    {c.name}: originality {meta['originality_score']} -> {meta['originality_reasons']}")
    print("Nothing was uploaded. Only original synthetic media used.")


if __name__ == "__main__":
    main()
