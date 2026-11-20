"""Optional VoiceStudio engine (https://github.com/debpalash/VoiceStudio), reached over HTTP only.

VoiceStudio is AGPL-3.0. We never import, vendor or modify its code: it runs as its own unmodified
service (desktop app or Docker image) and we call its OpenAI-compatible endpoints:
  POST /v1/audio/speech          text -> wav   (voice = a VoiceStudio profile id/name, or "default")
  POST /v1/audio/transcriptions  wav  -> text
  GET  /health, /v1/audio/voices

Model WEIGHTS carry their own licences, separate from the app. The default OmniVoice weights are
CC-BY-NC (non-commercial). For a monetized channel or the paid web app pick a commercially licensed
engine in VoiceStudio's Model Manager (e.g. Kokoro / CosyVoice3 / VoxCPM2 - check the model card).

Config (env wins over config/settings.json -> voice.voicestudio):
  VOICESTUDIO_URL      e.g. http://127.0.0.1:3900   (empty = disabled)
  VOICESTUDIO_API_KEY  only needed when VoiceStudio is not on this machine
  voice.voicestudio.model / .voice / .voices{lang: profile}
"""
from __future__ import annotations

import ipaddress
import json
import socket
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from .common import env, settings

TIMEOUT = 300  # first request may warm a model


class VoiceStudioError(RuntimeError):
    pass


def config() -> dict:
    cfg = dict(settings().get("voice", {}).get("voicestudio", {}) or {})
    cfg["url"] = (env("VOICESTUDIO_URL") or cfg.get("url") or "").strip().rstrip("/")
    cfg["api_key"] = env("VOICESTUDIO_API_KEY")
    cfg.setdefault("model", "")
    cfg.setdefault("voice", "default")
    cfg.setdefault("voices", {})
    return cfg


def enabled() -> bool:
    return bool(config()["url"])


def _is_local(host: str) -> bool:
    if host in ("localhost",):
        return True
    try:
        infos = {i[4][0] for i in socket.getaddrinfo(host, None)}
    except OSError:
        return False
    try:
        return bool(infos) and all(ipaddress.ip_address(a.split("%")[0]).is_loopback for a in infos)
    except ValueError:
        return False


def _base() -> tuple[str, dict]:
    cfg = config()
    url = cfg["url"]
    if not url:
        raise VoiceStudioError("VoiceStudio disabled: set VOICESTUDIO_URL (e.g. http://127.0.0.1:3900)")
    p = urllib.parse.urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise VoiceStudioError(f"VOICESTUDIO_URL must be http(s)://host:port, got {url!r}")
    headers = {"User-Agent": "trendtide"}
    if cfg["api_key"]:
        # a Bearer key must never cross a network in plaintext (VoiceStudio's own guidance)
        if p.scheme != "https" and not _is_local(p.hostname):
            raise VoiceStudioError("VOICESTUDIO_URL must be https:// when VOICESTUDIO_API_KEY is set for a remote host")
        headers["Authorization"] = f"Bearer {cfg['api_key']}"
    return url, headers


def _request(method: str, path: str, body: bytes | None = None, ctype: str | None = None,
             timeout: float = TIMEOUT) -> tuple[bytes, str]:
    url, headers = _base()
    if ctype:
        headers["Content-Type"] = ctype
    req = urllib.request.Request(url + path, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - operator-configured URL
            return resp.read(), resp.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:400].decode("utf-8", "replace")
        try:
            j = json.loads(detail)
            detail = (j.get("error") or {}).get("message") or j.get("detail") or detail
        except Exception:
            pass
        hint = ""
        if exc.code == 403:
            hint = " (accept the model licence in VoiceStudio's Model Manager, or check the API key)"
        elif exc.code == 401:
            hint = " (set VOICESTUDIO_API_KEY)"
        raise VoiceStudioError(f"VoiceStudio {path} HTTP {exc.code}: {detail}{hint}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise VoiceStudioError(f"VoiceStudio not reachable at {url} ({exc}). Start the app or the Docker service.") from exc


def health(timeout: float = 3) -> bool:
    try:
        _request("GET", "/health", timeout=timeout)
        return True
    except VoiceStudioError:
        return False


def voices() -> list:
    data, _ = _request("GET", "/v1/audio/voices", timeout=15)
    j = json.loads(data or b"{}")
    return j.get("voices", j.get("data", [])) if isinstance(j, dict) else j


def voice_for(lang: str | None) -> str:
    cfg = config()
    return (cfg["voices"] or {}).get(lang or "", "") or cfg["voice"] or "default"


def speak(text: str, out: Path, lang: str | None = None, voice: str | None = None) -> Path:
    """Synthesize `text` to a WAV at `out`. The voice must be a stock voice or a profile of a voice
    you own / have consent for - VoiceStudio records consent on cloned profiles."""
    if not text.strip():
        raise VoiceStudioError("empty text")
    cfg = config()
    body = {"input": text[:4096], "voice": voice or voice_for(lang), "response_format": "wav"}
    if cfg["model"]:
        body["model"] = cfg["model"]
    if lang:
        body["language"] = lang
    data, ctype = _request("POST", "/v1/audio/speech", json.dumps(body).encode(), "application/json")
    if len(data) < 1000 or not data[:4] == b"RIFF":
        raise VoiceStudioError(f"VoiceStudio returned no audio ({ctype}, {len(data)} bytes)")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".part")
    tmp.write_bytes(data)
    tmp.replace(out)
    return out


def transcribe(audio: Path, model: str = "", language: str = "") -> str:
    boundary = uuid.uuid4().hex
    parts = []
    for k, v in (("model", model), ("language", language), ("response_format", "json")):
        if v:
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{audio.name}"\r\n'
                  "Content-Type: application/octet-stream\r\n\r\n").encode() + audio.read_bytes() + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    data, _ = _request("POST", "/v1/audio/transcriptions", b"".join(parts), f"multipart/form-data; boundary={boundary}")
    try:
        return json.loads(data).get("text", "")
    except ValueError:
        return data.decode("utf-8", "replace")
