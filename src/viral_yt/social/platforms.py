"""Platform adapters (official APIs only). Each adapter:
   auth_url(state, redirect, verifier) -> url        exchange(code, redirect, verifier) -> account dict
   refresh(token) -> token                            publish(token, account_id, video, meta) -> result dict
Constraints worth knowing (enforced by the platforms, not by us):
 * YouTube: apps that haven't passed Google's API audit get uploads locked PRIVATE.
 * TikTok: unaudited apps can only post to PRIVATE accounts / SELF_ONLY, max 5 users per 24 h.
 * Instagram: Business or Creator account; app needs instagram_business_content_publish approved by Meta.
"""
from __future__ import annotations

import base64
import hashlib
import json
import time
import urllib.parse
from pathlib import Path

from ..common import ROOT, env
from .http import Http, PlatformError


def pkce_challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def _google_client() -> tuple[str, str]:
    cid, sec = env("YT_CLIENT_ID"), env("YT_CLIENT_SECRET")
    if cid and sec:
        return cid, sec
    p = Path(env("GOOGLE_OAUTH_WEB_CLIENT", ".secrets/google_web_client.json"))
    p = p if p.is_absolute() else ROOT / p
    if p.is_file():
        d = json.loads(p.read_text()).get("web", {})
        return d.get("client_id", ""), d.get("client_secret", "")
    return "", ""


class YouTube:
    key, label = "youtube", "YouTube"
    SCOPES = "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly"

    def __init__(self, http: Http | None = None):
        self.http = http or Http()

    def configured(self) -> bool:
        return all(_google_client())

    def auth_url(self, state: str, redirect: str, verifier: str) -> str:
        cid, _ = _google_client()
        return "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
            "client_id": cid, "redirect_uri": redirect, "response_type": "code", "scope": self.SCOPES,
            "access_type": "offline", "prompt": "consent", "include_granted_scopes": "true", "state": state,
            "code_challenge": pkce_challenge(verifier), "code_challenge_method": "S256"})

    def exchange(self, code: str, redirect: str, verifier: str) -> dict:
        cid, sec = _google_client()
        tok = self.http.call("POST", "https://oauth2.googleapis.com/token", form={
            "code": code, "client_id": cid, "client_secret": sec, "redirect_uri": redirect,
            "grant_type": "authorization_code", "code_verifier": verifier})
        tok["expires_at"] = time.time() + int(tok.get("expires_in", 3600)) - 60
        ch = self.http.call("GET", "https://www.googleapis.com/youtube/v3/channels",
                            params={"part": "snippet", "mine": "true"},
                            headers={"Authorization": f"Bearer {tok['access_token']}"}).get("items", [])
        if not ch:
            raise PlatformError("This Google account has no YouTube channel")
        return {"account_id": ch[0]["id"], "handle": ch[0]["snippet"].get("customUrl") or ch[0]["snippet"]["title"],
                "token": tok}

    def refresh(self, tok: dict) -> dict:
        if tok.get("expires_at", 0) > time.time():
            return tok
        cid, sec = _google_client()
        new = self.http.call("POST", "https://oauth2.googleapis.com/token", form={
            "client_id": cid, "client_secret": sec, "refresh_token": tok["refresh_token"], "grant_type": "refresh_token"})
        tok.update(access_token=new["access_token"], expires_at=time.time() + int(new.get("expires_in", 3600)) - 60)
        return tok

    def publish(self, tok: dict, account_id: str, video: Path, meta: dict) -> dict:
        body = {"snippet": {"title": meta["title"][:100], "description": meta.get("description", "")[:4900],
                            "tags": meta.get("tags", [])[:15], "categoryId": meta.get("category_id", "22")},
                "status": {"privacyStatus": meta.get("privacy", "private"), "selfDeclaredMadeForKids": False,
                           "containsSyntheticMedia": bool(meta.get("synthetic"))}}
        size = video.stat().st_size
        auth = {"Authorization": f"Bearer {tok['access_token']}"}
        self.http.call("POST", "https://www.googleapis.com/upload/youtube/v3/videos",
                       params={"uploadType": "resumable", "part": "snippet,status"}, json_body=body,
                       headers={**auth, "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(size)})
        session = getattr(self.http, "last_headers", {}).get("location")
        if not session:
            raise PlatformError("YouTube did not return an upload session")
        res = self.http.call("PUT", session, data=video.read_bytes(),
                             headers={**auth, "Content-Type": "video/mp4", "Content-Length": str(size)}, timeout=1800)
        vid = res.get("id")
        if not vid:
            raise PlatformError(f"YouTube upload failed: {res}")
        return {"remote_id": vid, "url": f"https://www.youtube.com/shorts/{vid}",
                "status": res.get("status", {}).get("privacyStatus", body["status"]["privacyStatus"])}


class Instagram:
    key, label = "instagram", "Instagram"
    VER = property(lambda self: env("IG_API_VERSION", "v26.0"))
    SCOPES = "instagram_business_basic,instagram_business_content_publish"

    def __init__(self, http: Http | None = None):
        self.http = http or Http()

    def configured(self) -> bool:
        return bool(env("IG_APP_ID") and env("IG_APP_SECRET"))

    def auth_url(self, state: str, redirect: str, verifier: str) -> str:
        return "https://www.instagram.com/oauth/authorize?" + urllib.parse.urlencode({
            "client_id": env("IG_APP_ID"), "redirect_uri": redirect, "response_type": "code",
            "scope": self.SCOPES, "state": state})

    def exchange(self, code: str, redirect: str, verifier: str) -> dict:
        short = self.http.call("POST", "https://api.instagram.com/oauth/access_token", form={
            "client_id": env("IG_APP_ID"), "client_secret": env("IG_APP_SECRET"), "grant_type": "authorization_code",
            "redirect_uri": redirect, "code": code})
        long = self.http.call("GET", "https://graph.instagram.com/access_token", params={
            "grant_type": "ig_exchange_token", "client_secret": env("IG_APP_SECRET"), "access_token": short["access_token"]})
        tok = {"access_token": long["access_token"], "expires_at": time.time() + int(long.get("expires_in", 5_184_000)) - 86400}
        me = self.http.call("GET", f"https://graph.instagram.com/{self.VER}/me",
                            params={"fields": "user_id,username", "access_token": tok["access_token"]})
        return {"account_id": str(me.get("user_id") or me.get("id") or short.get("user_id")),
                "handle": "@" + me.get("username", ""), "token": tok}

    def refresh(self, tok: dict) -> dict:
        if tok.get("expires_at", 0) - time.time() > 7 * 86400:
            return tok
        new = self.http.call("GET", "https://graph.instagram.com/refresh_access_token",
                             params={"grant_type": "ig_refresh_token", "access_token": tok["access_token"]})
        tok.update(access_token=new["access_token"], expires_at=time.time() + int(new.get("expires_in", 5_184_000)) - 86400)
        return tok

    def publish(self, tok: dict, account_id: str, video: Path, meta: dict, poll_s: float = 5, max_wait: int = 600) -> dict:
        at, base = tok["access_token"], f"https://graph.instagram.com/{self.VER}"
        if not 3 <= meta.get("duration", 30) <= 900:
            raise PlatformError("Instagram Reels must be between 3 seconds and 15 minutes")
        c = self.http.call("POST", f"{base}/{account_id}/media", params={
            "media_type": "REELS", "upload_type": "resumable", "caption": meta.get("caption", "")[:2200],
            "share_to_feed": "true", "access_token": at})
        cid = c.get("id")
        if not cid:
            raise PlatformError(f"Instagram container failed: {c}")
        data = video.read_bytes()
        self.http.call("POST", f"https://rupload.facebook.com/ig-api-upload/{self.VER}/{cid}", data=data,
                       headers={"Authorization": f"OAuth {at}", "offset": "0", "file_size": str(len(data))}, timeout=1800)
        waited = 0.0
        while True:
            st = self.http.call("GET", f"{base}/{cid}", params={"fields": "status_code,status", "access_token": at})
            code = st.get("status_code")
            if code == "FINISHED":
                break
            if code in ("ERROR", "EXPIRED") or waited > max_wait:
                raise PlatformError(f"Instagram processing {code or 'timeout'}: {st.get('status', '')}")
            time.sleep(poll_s)
            waited += poll_s
        pub = self.http.call("POST", f"{base}/{account_id}/media_publish", params={"creation_id": cid, "access_token": at})
        mid = pub.get("id")
        if not mid:
            raise PlatformError(f"Instagram publish failed: {pub}")
        link = self.http.call("GET", f"{base}/{mid}", params={"fields": "permalink", "access_token": at}).get("permalink", "")
        return {"remote_id": mid, "url": link, "status": "published"}


class TikTok:
    key, label = "tiktok", "TikTok"
    API = "https://open.tiktokapis.com/v2"
    SCOPES = "user.info.basic,video.publish"

    def __init__(self, http: Http | None = None):
        self.http = http or Http()

    def configured(self) -> bool:
        return bool(env("TIKTOK_CLIENT_KEY") and env("TIKTOK_CLIENT_SECRET"))

    def auth_url(self, state: str, redirect: str, verifier: str) -> str:
        return "https://www.tiktok.com/v2/auth/authorize/?" + urllib.parse.urlencode({
            "client_key": env("TIKTOK_CLIENT_KEY"), "scope": self.SCOPES, "response_type": "code",
            "redirect_uri": redirect, "state": state, "code_challenge": pkce_challenge(verifier),
            "code_challenge_method": "S256"})

    def exchange(self, code: str, redirect: str, verifier: str) -> dict:
        tok = self.http.call("POST", f"{self.API}/oauth/token/", form={
            "client_key": env("TIKTOK_CLIENT_KEY"), "client_secret": env("TIKTOK_CLIENT_SECRET"), "code": code,
            "grant_type": "authorization_code", "redirect_uri": redirect, "code_verifier": verifier})
        if "access_token" not in tok:
            raise PlatformError(f"TikTok login failed: {tok.get('error_description') or tok}")
        tok["expires_at"] = time.time() + int(tok.get("expires_in", 86400)) - 60
        info = self.http.call("GET", f"{self.API}/user/info/", params={"fields": "open_id,display_name"},
                              headers={"Authorization": f"Bearer {tok['access_token']}"}).get("data", {}).get("user", {})
        return {"account_id": tok.get("open_id") or info.get("open_id", ""), "handle": info.get("display_name", "TikTok"),
                "token": tok}

    def refresh(self, tok: dict) -> dict:
        if tok.get("expires_at", 0) > time.time():
            return tok
        new = self.http.call("POST", f"{self.API}/oauth/token/", form={
            "client_key": env("TIKTOK_CLIENT_KEY"), "client_secret": env("TIKTOK_CLIENT_SECRET"),
            "grant_type": "refresh_token", "refresh_token": tok["refresh_token"]})
        tok.update(access_token=new["access_token"], refresh_token=new.get("refresh_token", tok["refresh_token"]),
                   expires_at=time.time() + int(new.get("expires_in", 86400)) - 60)
        return tok

    def creator_info(self, tok: dict) -> dict:
        r = self.http.call("POST", f"{self.API}/post/publish/creator_info/query/", json_body={},
                           headers={"Authorization": f"Bearer {tok['access_token']}"})
        if r.get("error", {}).get("code") not in (None, "ok"):
            raise PlatformError(f"TikTok: {r['error'].get('message') or r['error'].get('code')}")
        return r.get("data", {})

    def publish(self, tok: dict, account_id: str, video: Path, meta: dict, poll_s: float = 5, max_wait: int = 600) -> dict:
        auth = {"Authorization": f"Bearer {tok['access_token']}"}
        info = self.creator_info(tok)
        if meta.get("duration", 0) > info.get("max_video_post_duration_sec", 600):
            raise PlatformError("Clip is longer than this TikTok account allows")
        privacy = meta.get("tiktok_privacy") or "SELF_ONLY"
        if privacy not in info.get("privacy_level_options", [privacy]):
            raise PlatformError(f"TikTok privacy '{privacy}' not available for this account "
                                f"(options: {info.get('privacy_level_options')}). Unaudited apps can only post privately.")
        size = video.stat().st_size
        chunk = size if size <= 64 * 1024 * 1024 else 10 * 1024 * 1024
        total = 1 if chunk == size else size // chunk
        r = self.http.call("POST", f"{self.API}/post/publish/video/init/", headers=auth, json_body={
            "post_info": {"title": meta.get("caption", "")[:2200], "privacy_level": privacy,
                          "disable_comment": False, "disable_duet": False, "disable_stitch": False,
                          "video_cover_timestamp_ms": 1000, "is_aigc": bool(meta.get("synthetic"))},
            "source_info": {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk, "total_chunk_count": total}})
        if r.get("error", {}).get("code") not in (None, "ok"):
            raise PlatformError(f"TikTok: {r['error'].get('message') or r['error'].get('code')}")
        data = r["data"]
        with video.open("rb") as f:
            for i in range(total):
                start = i * chunk
                part = f.read(chunk if i < total - 1 else size - start)
                self.http.call("PUT", data["upload_url"], data=part, timeout=1800, headers={
                    "Content-Type": "video/mp4", "Content-Length": str(len(part)),
                    "Content-Range": f"bytes {start}-{start + len(part) - 1}/{size}"})
        waited = 0.0
        while True:
            st = self.http.call("POST", f"{self.API}/post/publish/status/fetch/", headers=auth,
                                json_body={"publish_id": data["publish_id"]}).get("data", {})
            if st.get("status") == "PUBLISH_COMPLETE":
                return {"remote_id": data["publish_id"], "url": "", "status": privacy.lower()}
            if st.get("status") == "FAILED" or waited > max_wait:
                raise PlatformError(f"TikTok publish {st.get('fail_reason') or 'timeout'}")
            time.sleep(poll_s)
            waited += poll_s


PLATFORMS = {"youtube": YouTube, "instagram": Instagram, "tiktok": TikTok}
