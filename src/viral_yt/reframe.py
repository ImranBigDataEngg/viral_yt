"""Shot-aware smart reframing for 9:16, 1:1 and 16:9 outputs, with ACTIVE-SPEAKER following.

One low-res FFmpeg decode per clip gives scene cuts (scdet) and 6 fps grey frames. YuNet (230 KB, MIT) finds faces
and mouth landmarks (Haar fallback on OpenCV 4.x). Per shot:
  * one face (or one dominant face)      -> locked crop on that face
  * two or more people + speech           -> follow whoever is talking: mouth-motion energy during speech
                                             (audio RMS) picks the speaker per 1-s window; hysteresis (>= 1.5 s
                                             holds, 1.3x margin) and HARD cuts between speakers, never pans
  * nobody talking / gameplay / no faces  -> blur-fit (whole frame kept)
"""
from __future__ import annotations

import json
import re
import subprocess
import wave
from pathlib import Path
from statistics import median

ANALYSIS_H = 360
SAMPLE_FPS = 6
MIN_SHOT_S = 0.8
SPEAKER_WINDOW_S = 1.0
SPEAKER_MIN_HOLD_S = 1.5
SPEAKER_MARGIN = 1.3
YUNET = "models/face_detection_yunet_2023mar.onnx"   # MIT, 230 KB, from opencv_zoo


def probe_size(source: Path) -> tuple[int, int]:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                        "-of", "json", str(source)], capture_output=True, text=True)
    s = json.loads(r.stdout or "{}").get("streams", [{}])[0]
    return int(s.get("width", 1920)), int(s.get("height", 1080))


def _decode(source: Path, a: float, d: float, workdir: Path, aw: int):
    import numpy as np
    import uuid
    name = f"scd_{uuid.uuid4().hex[:10]}.txt"          # unique: clips are analysed in parallel threads
    shots_file = workdir / name
    fc = (f"[0:v]scale={aw}:{ANALYSIS_H},split[s][f];"
          f"[s]scdet=threshold=12,metadata=print:key=lavfi.scd.time:file={name},nullsink;"
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


def _faces(frames):
    """Per frame: list of (cx, cy, w, mx0, my0, mx1, my1) in analysis px (mouth box). None if no detector."""
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
    out = []
    if model.is_file() and hasattr(cv2, "FaceDetectorYN"):
        det = cv2.FaceDetectorYN.create(str(model), "", (w, h), 0.75, 0.3, 50)
        for f in frames:
            _, found = det.detect(cv2.cvtColor(f, cv2.COLOR_GRAY2BGR))
            faces = []
            for row in (found if found is not None else []):
                x, y, fw, fh = row[:4]
                rmx, rmy, lmx, lmy = row[10:14]          # mouth corners
                mw = max(abs(lmx - rmx), fw * 0.25)
                my = (rmy + lmy) / 2
                faces.append((float(x + fw / 2), float(y + fh / 2), float(fw), float(min(rmx, lmx) - mw * 0.2),
                              float(my - fh * 0.12), float(max(rmx, lmx) + mw * 0.2), float(my + fh * 0.14)))
            out.append(faces)
        return out
    xml = Path(getattr(getattr(cv2, "data", None), "haarcascades", "") or "") / "haarcascade_frontalface_default.xml"
    if not xml.is_file():
        return None
    front = cv2.CascadeClassifier(str(xml))
    for f in frames:
        found = front.detectMultiScale(cv2.equalizeHist(f), 1.1, 6, minSize=(28, 28))
        out.append([(x + fw / 2, y + fh / 2, fw, x + fw * .25, y + fh * .68, x + fw * .75, y + fh * .95)
                    for x, y, fw, fh in found])
    return out


def mouth_motion(frames, faces) -> list[list[float]]:
    """Mean absolute frame difference inside each face's mouth box (same index order as faces)."""
    import numpy as np
    out = []
    for i, fs in enumerate(faces):
        row = []
        for f in fs:
            if i == 0:
                row.append(0.0)
                continue
            x0, y0, x1, y1 = (int(max(0, v)) for v in f[3:7])
            a = frames[i][y0:y1, x0:x1].astype(np.int16)
            b = frames[i - 1][y0:y1, x0:x1].astype(np.int16)
            row.append(float(np.abs(a - b).mean()) if a.size and a.shape == b.shape else 0.0)
        out.append(row)
    return out


def speech_activity(wav16k: Path | None, a: float, n: int) -> list[bool] | None:
    """Per sample: is someone speaking? (RMS above an adaptive floor). None without audio."""
    if not wav16k or not wav16k.is_file():
        return None
    import numpy as np
    with wave.open(str(wav16k)) as w:
        sr = w.getframerate()
        w.setpos(min(w.getnframes(), int(a * sr)))
        data = np.frombuffer(w.readframes(int(n / SAMPLE_FPS * sr) + sr), dtype=np.int16).astype(np.float32)
    hop = sr // SAMPLE_FPS
    rms = np.array([np.sqrt(np.mean(data[i * hop:(i + 1) * hop] ** 2) + 1e-6) for i in range(n)])
    if not len(rms):
        return None
    # threshold between the quiet floor and the loud level; works for pauses AND for continuous talk
    thr = max(120.0, min(np.percentile(rms, 20) * 2.0, np.percentile(rms, 90) * 0.5))
    return [bool(r > thr) for r in rms]


def _tracks(samples, aw: int) -> list[float]:
    xs = sorted(f[0] for fs in samples for f in fs)
    if not xs:
        return []
    clusters, cur = [], [xs[0]]
    for x in xs[1:]:
        if x - cur[-1] > aw * 0.08:
            clusters.append(cur)
            cur = []
        cur.append(x)
    clusters.append(cur)
    need = max(2, int(len(samples) * 0.2))
    return [sum(c) / len(c) for c in clusters if len(c) >= need]


def speaker_timeline(samples, motion, speech, tracks, aw: int) -> list[int | None]:
    """Pure: per window -> index of the active track (None = undecided). Smoothed with hysteresis."""
    win = max(1, int(SPEAKER_WINDOW_S * SAMPLE_FPS))
    raw = []
    for w0 in range(0, len(samples), win):
        score = [0.0] * len(tracks)
        for i in range(w0, min(len(samples), w0 + win)):
            if speech is not None and i < len(speech) and not speech[i]:
                continue
            for k, f in enumerate(samples[i]):
                t = min(range(len(tracks)), key=lambda j: abs(tracks[j] - f[0]))
                if abs(tracks[t] - f[0]) < aw * 0.1 and k < len(motion[i]):
                    score[t] += motion[i][k]
        best = sorted(range(len(tracks)), key=lambda j: score[j], reverse=True)
        if score[best[0]] > 0.5 and (len(best) == 1 or score[best[0]] >= SPEAKER_MARGIN * score[best[1]]):
            raw.append(best[0])
        else:
            raw.append(None)
    first = next((r for r in raw if r is not None), None)
    filled, cur = [], first
    for r in raw:
        cur = r if r is not None else cur
        filled.append(cur)
    hold = max(1, round(SPEAKER_MIN_HOLD_S / SPEAKER_WINDOW_S))
    i = 0
    while i < len(filled):                       # drop speaker runs shorter than the hold time
        j = i
        while j < len(filled) and filled[j] == filled[i]:
            j += 1
        if j - i < hold and i > 0:
            for k in range(i, j):
                filled[k] = filled[i - 1]
        i = j
    return filled


def _crop_x(cx_src: float, cw: int, src_w: int) -> int:
    x = int(max(0, min(src_w - cw, cx_src - cw / 2)))
    return x - x % 2


def plan_segments(duration: float, cuts: list[float], faces_per_sample, src_w: int, src_h: int, aw: int,
                  out_ratio: float = 9 / 16, motion=None, speech=None) -> list[dict]:
    """Pure function (unit-tested): shots + face samples (+ mouth motion + speech) -> framing segments."""
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
    src_ratio = src_w / src_h
    if out_ratio >= src_ratio - 0.01:            # output as wide or wider than source: nothing to crop
        return [{"start": 0.0, "end": round(duration, 3), "mode": "fit" if out_ratio > src_ratio + 0.01 else "crop",
                 "x": 0, "cw": src_w, "ch": src_h}]
    cw = min(src_w, int(round(src_h * out_ratio / 2)) * 2)
    segs: list[dict] = []

    def push(seg):
        if segs and segs[-1]["mode"] == seg["mode"] and abs(segs[-1]["x"] - seg["x"]) <= src_w * 0.04:
            segs[-1]["end"] = seg["end"]
        else:
            segs.append(seg)

    for s, e in shots:
        base = {"start": round(s, 3), "end": round(e, 3), "mode": "fit", "x": 0, "cw": cw, "ch": src_h}
        if faces_per_sample is None:
            push(base)
            continue
        i0, i1 = int(s * SAMPLE_FPS), min(len(faces_per_sample), int(e * SAMPLE_FPS) + 1)
        samples = faces_per_sample[i0:i1]
        with_face = [fs for fs in samples if fs]
        if not samples or len(with_face) / len(samples) < 0.4:
            push(base)
            continue
        wide = sum(1 for fs in with_face if len(fs) > 1 and
                   (max(f[0] for f in fs) - min(f[0] for f in fs)) * scale > cw * 0.8)
        if wide / len(with_face) < 0.5:          # one dominant subject
            cx = median(max(fs, key=lambda f: f[2])[0] for fs in with_face) * scale
            push({**base, "mode": "crop", "x": _crop_x(cx, cw, src_w)})
            continue
        tracks = _tracks(samples, aw)
        if motion is None or len(tracks) < 2:
            push(base)
            continue
        timeline = speaker_timeline(samples, motion[i0:i1], speech[i0:i1] if speech else None, tracks, aw)
        if all(t is None for t in timeline):
            push(base)
            continue
        for k, who in enumerate(timeline):
            ws, we = s + k * SPEAKER_WINDOW_S, min(e, s + (k + 1) * SPEAKER_WINDOW_S)
            if we - ws <= 0:
                continue
            seg = {**base, "start": round(ws, 3), "end": round(we, 3)}
            if who is not None:
                seg.update(mode="crop", x=_crop_x(tracks[who] * scale, cw, src_w), speaker=who)
            push(seg)
    if segs:
        segs[-1]["end"] = round(duration, 3)
    return segs


def analyze(source: Path, a: float, d: float, workdir: Path, out_ratio: float = 9 / 16,
            wav16k: Path | None = None) -> list[dict]:
    src_w, src_h = probe_size(source)
    if out_ratio >= src_w / src_h - 0.01:
        return plan_segments(d, [], None, src_w, src_h, max(2, src_w), out_ratio)
    if src_w / src_h <= 0.7:                       # vertical source into a narrower-or-equal frame
        cw = min(src_w, int(round(src_h * out_ratio / 2)) * 2)
        return [{"start": 0.0, "end": round(d, 3), "mode": "crop", "x": (src_w - cw) // 2, "cw": cw, "ch": src_h}]
    aw = int(round(ANALYSIS_H * src_w / src_h / 2)) * 2
    try:
        frames, cuts = _decode(source, a, d, workdir, aw)
    except Exception:
        frames, cuts = [], []
    faces = _faces(frames) if frames else None
    motion = mouth_motion(frames, faces) if faces else None
    speech = speech_activity(wav16k, a, len(frames)) if frames else None
    return plan_segments(d, cuts, faces, src_w, src_h, aw, out_ratio, motion, speech)


CORNERS = {"tl": (0.0, 0.0), "tr": (1.0, 0.0), "bl": (0.0, 1.0), "br": (1.0, 1.0)}


def find_facecam(source: Path, a: float, d: float, workdir: Path, corner: str = "auto") -> tuple[int, int, int, int]:
    """Webcam overlay rectangle (x, y, w, h) in source pixels for the streamer 'facecam split' layout.
    auto: a face that stays in the same spot near the frame edge in most sampled frames is the webcam
    (game characters move); otherwise fall back to the chosen corner (default bottom-left)."""
    sw, sh = probe_size(source)
    if corner in CORNERS:
        return _corner_box(corner, sw, sh)
    aw = int(round(ANALYSIS_H * sw / sh / 2)) * 2
    try:
        frames, _ = _decode(source, a, min(d, 60.0), workdir, aw)
        step = max(1, len(frames) // 12)
        frames = frames[::step][:12]
        faces = _faces(frames) if frames else None
    except Exception:
        faces = None
    if faces:
        cells: dict[tuple[int, int], list] = {}
        for fl in faces:
            for cx, cy, fw, *_ in fl:
                edge = min(cx, aw - cx) < aw * 0.30 or min(cy, ANALYSIS_H - cy) < ANALYSIS_H * 0.30
                if edge and fw < ANALYSIS_H * 0.45:
                    cells.setdefault((int(cx // (aw / 8)), int(cy // (ANALYSIS_H / 6))), []).append((cx, cy, fw))
        if cells:
            hits = max(cells.values(), key=len)
            if len(hits) >= max(2, len(faces) // 3):
                k = sw / aw
                cx = sorted(h[0] for h in hits)[len(hits) // 2] * k
                cy = sorted(h[1] for h in hits)[len(hits) // 2] * k
                fw = sorted(h[2] for h in hits)[len(hits) // 2] * k
                bw, bh = min(sw, fw * 3.4), min(sh, fw * 2.6)
                x = int(max(0, min(sw - bw, cx - bw / 2)))
                y = int(max(0, min(sh - bh, cy - bh / 2 + fw * 0.15)))
                return x // 2 * 2, y // 2 * 2, int(bw) // 2 * 2, int(bh) // 2 * 2
    return _corner_box("bl", sw, sh)


def _corner_box(corner: str, sw: int, sh: int) -> tuple[int, int, int, int]:
    fx, fy = CORNERS[corner]
    bw, bh = int(sw * 0.30) // 2 * 2, int(sh * 0.30) // 2 * 2
    return int((sw - bw) * fx) // 2 * 2, int((sh - bh) * fy) // 2 * 2, bw, bh
