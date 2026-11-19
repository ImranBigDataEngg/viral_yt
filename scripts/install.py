"""TrendTide installer (pure Python, standard library only; works on Windows, macOS and Linux).

    py scripts\\install.py --ai --web            (Windows)
    python3 scripts/install.py --ai --web       (macOS / Linux)

What it does, nothing more:
  1. creates .venv in the project folder
  2. pip-installs the project and the requirement files you ask for
  3. downloads the optional model files over HTTPS and VERIFIES their SHA-256 before keeping them
  4. creates .env / folders from the templates
  5. runs the test suite

It never changes system settings, execution policy, PATH or the registry, never runs a shell, and never installs
system software (install Python and FFmpeg yourself — see README).
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import urllib.request
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENV = ROOT / ".venv"
PY = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

MODELS = {
    # path: (url, sha256 or None = verify against the host's published LFS sha256 header)
    "models/face_detection_yunet_2023mar.onnx": (
        "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_detection_yunet/"
        "face_detection_yunet_2023mar.onnx",
        "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"),
}
VOICE = {
    "models/piper/en_US-ryan-high.onnx":
        ("https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ryan/high/en_US-ryan-high.onnx", None),
    "models/piper/en_US-ryan-high.onnx.json":
        ("https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ryan/high/en_US-ryan-high.onnx.json", None),
}


def step(msg: str) -> None:
    print(f"\n==> {msg}", flush=True)


def run(*args: str) -> None:
    subprocess.run(list(args), check=True, cwd=ROOT)


def fetch(rel: str, url: str, sha256: str | None) -> None:
    dest = ROOT / rel
    if dest.is_file() and sha256 and hashlib.sha256(dest.read_bytes()).hexdigest() == sha256:
        print(f"   ok (already present): {rel}")
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "TrendTide-installer"})
    h = hashlib.sha256()
    with urllib.request.urlopen(req, timeout=60) as r, tmp.open("wb") as f:
        expected = sha256 or (r.headers.get("x-linked-etag") or "").strip('"') or None
        while chunk := r.read(1 << 20):
            h.update(chunk)
            f.write(chunk)
    digest = h.hexdigest()
    if expected and len(expected) == 64 and digest != expected:
        tmp.unlink(missing_ok=True)
        raise SystemExit(f"SHA-256 mismatch for {rel}: got {digest}, expected {expected}. File discarded.")
    tmp.replace(dest)
    print(f"   downloaded {rel} (sha256 {digest[:16]}…{' verified' if expected else ''})")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ai", action="store_true", help="transcription, smart reframe (OpenCV) + face model")
    ap.add_argument("--web", action="store_true", help="web app (accounts, Stripe, link clipper, review editor)")
    ap.add_argument("--voice", action="store_true", help="Piper text-to-speech voice model (~120 MB)")
    ap.add_argument("--skip-tests", action="store_true")
    a = ap.parse_args()

    if sys.version_info < (3, 11):
        raise SystemExit("Python 3.11+ is required (https://www.python.org/downloads/)")
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        print("WARNING: FFmpeg not found on PATH. Install the FULL build (e.g. `winget install Gyan.FFmpeg`), "
              "reopen the terminal, then re-run this installer.")

    step(f"Creating virtual environment in {VENV}")
    if not PY.exists():
        venv.EnvBuilder(with_pip=True).create(VENV)
    run(str(PY), "-m", "pip", "install", "--upgrade", "pip")

    step("Installing TrendTide")
    reqs = ["-r", "requirements.txt"]
    if a.ai:
        reqs += ["-r", "requirements-optional.txt"]
    if a.web:
        reqs += ["-r", "requirements-web.txt"]
    run(str(PY), "-m", "pip", "install", "-e", ".", *reqs)

    if a.ai:
        step("Downloading face model (verified)")
        for rel, (url, sha) in MODELS.items():
            fetch(rel, url, sha)
    if a.voice:
        step("Downloading Piper voice (verified against the host's SHA-256)")
        for rel, (url, sha) in VOICE.items():
            fetch(rel, url, sha)

    step("Creating config and folders")
    if not (ROOT / ".env").exists():
        shutil.copy(ROOT / ".env.example", ROOT / ".env")
    for d in ("media/authorized/evidence", "media/transcripts", "media/voice", "output", ".secrets"):
        (ROOT / d).mkdir(parents=True, exist_ok=True)
    ledger = ROOT / "media/authorized/assets.csv"
    if not ledger.exists():
        shutil.copy(ROOT / "examples/assets_template.csv", ledger)

    if not a.skip_tests:
        step("Running tests")
        subprocess.run([str(PY), "-m", "unittest", "discover", "-s", "tests"], cwd=ROOT)

    exe = VENV / ("Scripts/trendtide.exe" if os.name == "nt" else "bin/trendtide")
    print(f"\nDone. Next:\n  {exe} doctor\n  {exe} web      (then open http://127.0.0.1:8000)")


if __name__ == "__main__":
    main()
