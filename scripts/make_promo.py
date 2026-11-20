"""Re-render the landing-page demo video (original animation, no game footage):
    py scripts\\make_promo.py
Edit scripts/promo_gta6.ass to change the text/timing. Output: src/viral_yt/web/static/demo/gta6_stream_demo.mp4/.jpg"""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
out = ROOT / "src/viral_yt/web/static/demo"
out.mkdir(parents=True, exist_ok=True)
subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                "gradients=s=720x1280:c0=0x1e1b4b:c1=0x0e7490:c2=0x3b0764:c3=0x000000:nb_colors=4:speed=0.012:r=30:d=13",
                "-vf", "noise=alls=5:allf=t,vignette=PI/5,ass=promo_gta6.ass,format=yuv420p", "-t", "13", "-an",
                "-c:v", "libx264", "-preset", "slow", "-crf", "27", "-movflags", "+faststart", str(out / "gta6_stream_demo.mp4")],
               check=True, cwd=ROOT / "scripts")
subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", "0.9", "-i", str(out / "gta6_stream_demo.mp4"),
                "-frames:v", "1", "-q:v", "4", str(out / "gta6_stream_demo.jpg")], check=True)
print("written", out / "gta6_stream_demo.mp4")
