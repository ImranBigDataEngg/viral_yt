"""Shot-aware smart reframing (16:9 -> 9:16) with zero heavy dependencies.

One low-res FFmpeg decode per clip gives BOTH scene cuts (scdet) and 3 fps grey frames; OpenCV's bundled
Haar cascades find faces. Each shot then gets ONE locked framing decision (never panning across a cut —
stillness beats a jittery tracker):
  crop -> 9:16 window centred on the dominant face (eye-line aware)
  fit  -> blur-fit (keeps the whole frame) for gameplay/screens, wide two-shots, or no faces
Without OpenCV everything falls back to fit. MediaPipe is not required (works on Python 3.13).
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from statistics import median

ANALYSIS_H = 360
SAMPLE_FPS = 3
MIN_SHOT_S = 0.8


def probe_size(source: Path) -> tuple[int, int]:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                        "-of", "json", str(source)], capture_output=True, text=True)
    s = json.loads(r.stdout or "{}").get("streams", [{}])[0]
    return int(s.get("width", 1920)), int(s.get("height", 1080))


def _decode(source: Path, a: float, d: float, workdir: Path, aw: int):
    import numpy as np
    shots_file = workdir / "scd.txt"
    fc = (f"[0:v]scale={aw}:{ANALYSIS_H},split[s][f];"
          f"[s]scdet=threshold=12,metadata=print:key=lavfi.scd.time:file=scd.txt,nullsink;"
          f"[f]fps={SAMPLE_FPS},format=gray[o]")
    proc = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{a:.3f}", "-t", f"{d:.3f}",
                           "-i", str(source), "-filter_complex", fc, "-map", "[o]", "-f", "rawvideo", "-"],
                          capture_output=True, cwd=str(workdir))
    frame = aw * ANALYSIS_H
    buf = proc.stdout
    frames = [np.frombuffer(buf[i:i + frame], dtype=np.uint8).reshape(ANALYSIS_H, aw)
              for i in range(0, len(buf) - frame + 1, frame)]
    cuts = []
    if shots_file.is_file():
        cuts = [float(m) for m in re.findall(r"lavfi\.scd\.time=([\d.]+)", shots_file.read_text())]
        shots_file.unlink()
    return frames, cuts


YUNET = "models/face_detection_yunet_2023mar.onnx"   # MIT, 230 KB, from opencv_zoo


def _faces(frames):
    """Per frame: list of (cx, cy, w) in analysis pixels. YuNet (accurate, fast) > Haar (OpenCV 4.x) > None."""
    try:
        import cv2
    except ImportError:
        return None
    from .common import ROOT
    try:
        cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)
    except Exception:
        pass
    h, w = frames[0].shape[:2]
    model = ROOT / YUNET
    if model.is_file() and hasattr(cv2, "FaceDetectorYN"):
        det = cv2.FaceDetectorYN.create(str(model), "", (w, h), 0.75, 0.3, 50)
        out = []
        for f in frames:
            _, found = det.detect(cv2.cvtColor(f, cv2.COLOR_GRAY2BGR))
            out.append([(float(x + fw / 2), float(y + fh / 2), float(fw)) for x, y, fw, fh, *_ in (found if found is not None else [])])
        return out
    xml = Path(getattr(getattr(cv2, "data", None), "haarcascades", "") or "") / "haarcascade_frontalface_default.xml"
    if not xml.is_file():
        return None
    front = cv2.CascadeClassifier(str(xml))
    out = []
    for f in frames:
        found = front.detectMultiScale(cv2.equalizeHist(f), 1.1, 6, minSize=(28, 28))
        out.append([(x + fw / 2, y + fh / 2, fw) for x, y, fw, fh in found])
    return out


def plan_segments(duration: float, cuts: list[float], faces_per_sample, src_w: int, src_h: int, aw: int,
                  out_ratio: float = 9 / 16) -> list[dict]:
    """Pure function (unit-tested): shots + face samples -> per-shot locked framing."""
    bounds = [0.0] + sorted(c for c in cuts if 0 < c < duration) + [duration]
    shots = []
    for s, e in zip(bounds, bounds[1:]):
        if shots and e - s < MIN_SHOT_S:
            shots[-1][1] = e
        else:
            shots.append([s, e])
    if len(shots) > 1 and shots[0][1] - shots[0][0] < MIN_SHOT_S:
        shots[1][0] = shots[0][0]
        shots.pop(0)
    scale = src_w / aw
    cw = min(src_w, int(round(src_h * out_ratio / 2)) * 2)
    segs = []
    for s, e in shots:
        seg = {"start": round(s, 3), "end": round(e, 3), "mode": "fit", "x": 0, "cw": cw, "ch": src_h}
        if faces_per_sample is not None and src_w / src_h > 1.0:
            idx = range(int(s * SAMPLE_FPS), min(len(faces_per_sample), int(e * SAMPLE_FPS) + 1))
            samples = [faces_per_sample[i] for i in idx]
            with_face = [fs for fs in samples if fs]
            if samples and len(with_face) / len(samples) >= 0.4:
                wide = sum(1 for fs in with_face if len(fs) > 1 and
                           (max(f[0] for f in fs) - min(f[0] for f in fs)) * scale > cw * 0.8)
                if wide / len(with_face) < 0.5:
                    cx = median(max(fs, key=lambda f: f[2])[0] for fs in with_face) * scale
                    x = int(max(0, min(src_w - cw, cx - cw / 2)))
                    seg.update(mode="crop", x=x - x % 2)
        elif src_w / src_h <= 1.0:
            seg.update(mode="crop", x=max(0, (src_w - cw) // 2))
        if segs and segs[-1]["mode"] == seg["mode"] and abs(segs[-1]["x"] - seg["x"]) <= src_w * 0.04:
            segs[-1]["end"] = seg["end"]          # same framing -> merge, fewer filter branches
        else:
            segs.append(seg)
    return segs


def analyze(source: Path, a: float, d: float, workdir: Path) -> list[dict]:
    src_w, src_h = probe_size(source)
    if src_w / src_h <= 0.7:                       # already vertical
        return [{"start": 0.0, "end": round(d, 3), "mode": "crop", "x": 0, "cw": src_w, "ch": src_h}]
    aw = int(round(ANALYSIS_H * src_w / src_h / 2)) * 2
    try:
        frames, cuts = _decode(source, a, d, workdir, aw)
    except Exception:
        frames, cuts = [], []
    faces = _faces(frames) if frames else None
    return plan_segments(d, cuts, faces, src_w, src_h, aw)
