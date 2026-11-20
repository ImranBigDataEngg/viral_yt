"""`trendtide doctor` — functional environment checks with exact fixes (probe, don't assume)."""
from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
import urllib.request

from .common import ROOT, env, settings

YUNET_URL = ("https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_detection_yunet/"
             "face_detection_yunet_2023mar.onnx")


def _ff_has(kind: str, name: str) -> bool:
    out = subprocess.run(["ffmpeg", "-hide_banner", f"-{kind}"], capture_output=True, text=True).stdout
    return f" {name} " in out


YUNET_SHA256 = "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"


def fetch_models() -> str:
    import hashlib
    from .reframe import YUNET
    target = ROOT / YUNET
    target.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(YUNET_URL, timeout=60) as r:
        data = r.read()
    if hashlib.sha256(data).hexdigest() != YUNET_SHA256:
        raise RuntimeError("Face model checksum mismatch - download discarded")
    target.write_bytes(data)
    return str(target)


def run_checks() -> list[dict]:
    rows = []

    def add(name, ok, detail, fix=""):
        rows.append({"check": name, "ok": bool(ok), "detail": detail, "fix": "" if ok else fix})

    add("python", sys.version_info >= (3, 11), sys.version.split()[0], "Install Python 3.11+")
    ff = shutil.which("ffmpeg") and shutil.which("ffprobe")
    add("ffmpeg+ffprobe", ff, shutil.which("ffmpeg") or "missing", "winget install Gyan.FFmpeg (FULL build, not Essentials)")
    if ff:
        add("libass (captions)", _ff_has("filters", "ass"), "ass filter", "Use the full FFmpeg build (Gyan.FFmpeg / brew ffmpeg-full)")
        add("scdet (shot detection)", _ff_has("filters", "scdet"), "scdet filter", "FFmpeg 4.4+ needed")
        add("flite (fallback TTS)", _ff_has("filters", "flite"), "optional", "Install Piper instead: pip install piper-tts")
        from .ffmpeg_tools import probe_hw_encoder
        enc = probe_hw_encoder(ROOT / "output" / ".encoder.json")
        add("GPU encoder", True, enc or "none working -> libx264 (fine, slower)")
    add("opencv (smart reframe)", importlib.util.find_spec("cv2"), "optional", "pip install opencv-python-headless")
    from .reframe import YUNET
    add("YuNet face model", (ROOT / YUNET).is_file(), str(ROOT / YUNET), "trendtide doctor --fetch-models")
    add("faster-whisper (ASR)", importlib.util.find_spec("faster_whisper"), "optional", "pip install -r requirements-optional.txt")
    add("piper (natural TTS)", importlib.util.find_spec("piper") or shutil.which("piper"), "optional", "py scripts/install.py --ai --voice")
    from . import voicestudio
    if voicestudio.enabled():
        vs = voicestudio.config()["url"]
        add("VoiceStudio (voices/dubbing)", voicestudio.health(), vs,
            "Start the VoiceStudio app or its Docker service, or clear VOICESTUDIO_URL (see docs/VOICESTUDIO.md)")
    add("google client (upload)", importlib.util.find_spec("googleapiclient"), "", "pip install -r requirements.txt")
    add("YOUTUBE_API_KEY", env("YOUTUBE_API_KEY"), "set" if env("YOUTUBE_API_KEY") else "missing", "Add to .env")
    add("OAuth client", (ROOT / env("YOUTUBE_CLIENT_SECRET", ".secrets/client_secret.json")).is_file(), ".secrets/client_secret.json",
        "Google Cloud > Credentials > OAuth client (Desktop app)")
    llm = settings()["llm"]
    if llm.get("engine") == "ollama":
        try:
            urllib.request.urlopen(llm["ollama_url"].rstrip("/") + "/api/tags", timeout=3)
            add("ollama", True, llm["ollama_url"])
        except Exception:
            add("ollama", False, llm["ollama_url"], "Install Ollama and run: ollama pull " + llm["ollama_model"])
    elif llm.get("engine") == "openai":
        add("LLM key", env(llm.get("api_key_env", "LLM_API_KEY")), llm.get("base_url", ""), f"Set {llm.get('api_key_env')} in .env")
    else:
        add("LLM re-rank", True, "off (heuristic only) — optional: set llm.engine to ollama/openai")
    return rows
