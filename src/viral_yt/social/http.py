"""Tiny JSON/form HTTP helper with an injectable opener (so tests can fake platform APIs)."""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request


class PlatformError(RuntimeError):
    pass


class Http:
    def __init__(self, opener=None):
        self.open = opener or urllib.request.urlopen

    def call(self, method: str, url: str, *, params: dict | None = None, form: dict | None = None,
             json_body: dict | None = None, data: bytes | None = None, headers: dict | None = None, timeout: int = 120):
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        h = dict(headers or {})
        if form is not None:
            data = urllib.parse.urlencode(form).encode()
            h.setdefault("Content-Type", "application/x-www-form-urlencoded")
        elif json_body is not None:
            data = json.dumps(json_body).encode()
            h.setdefault("Content-Type", "application/json; charset=UTF-8")
        req = urllib.request.Request(url, data=data, method=method, headers=h)
        try:
            with self.open(req, timeout=timeout) as r:
                raw = r.read()
                self.last_headers = {k.lower(): v for k, v in (r.headers.items() if r.headers else [])}
        except urllib.error.HTTPError as exc:
            body = exc.read()[:800].decode("utf-8", "replace")
            raise PlatformError(f"{method} {url.split('?')[0]} -> HTTP {exc.code}: {body}") from exc
        except urllib.error.URLError as exc:
            raise PlatformError(f"Network error: {exc.reason}") from exc
        try:
            return json.loads(raw or b"{}")
        except ValueError:
            return {"raw": raw[:300].decode("utf-8", "replace")}
