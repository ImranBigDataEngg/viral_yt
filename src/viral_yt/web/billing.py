"""Stripe Billing without the SDK: Checkout (hosted, card data never touches us = PCI SAQ A), Customer Portal,
and webhook signature verification. Entitlements change ONLY from verified webhooks, never from redirects."""
from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from .config import PLANS
from .db import DB, now
from ..common import env

API = "https://api.stripe.com/v1/"
ACTIVE = {"active", "trialing"}


class BillingError(RuntimeError):
    pass


def _flatten(d: dict, prefix: str = "") -> list[tuple[str, str]]:
    out = []
    for k, v in d.items():
        key = f"{prefix}[{k}]" if prefix else k
        if isinstance(v, dict):
            out += _flatten(v, key)
        elif isinstance(v, list):
            for i, item in enumerate(v):
                out += _flatten(item, f"{key}[{i}]") if isinstance(item, dict) else [(f"{key}[{i}]", str(item))]
        elif v is not None:
            out.append((key, str(v).lower() if isinstance(v, bool) else str(v)))
    return out


def stripe(secret: str, method: str, path: str, params: dict | None = None, idem: str | None = None) -> dict:
    if not secret:
        raise BillingError("Payments are not configured (STRIPE_SECRET_KEY missing)")
    data = urllib.parse.urlencode(_flatten(params or {})).encode() if method == "POST" else None
    url = API + path + ("?" + urllib.parse.urlencode(_flatten(params or {})) if method == "GET" and params else "")
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {secret}", "Stripe-Version": "2024-06-20",
        **({"Idempotency-Key": idem} if idem else {})})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as exc:
        msg = json.loads(exc.read() or b"{}").get("error", {}).get("message", "Stripe error")
        raise BillingError(msg) from exc


def price_id(plan: str) -> str:
    if plan not in PLANS:
        raise BillingError("Unknown plan")
    pid = env(PLANS[plan][2])
    if not pid.startswith("price_"):
        raise BillingError(f"{PLANS[plan][2]} is not configured")
    return pid


def plan_for_price(pid: str) -> str | None:
    return next((k for k, v in PLANS.items() if env(v[2]) == pid), None)


def checkout_url(db: DB, secret: str, base_url: str, user: dict, plan: str) -> str:
    params = {"mode": "subscription", "line_items": [{"price": price_id(plan), "quantity": 1}],
              "success_url": f"{base_url}/billing?status=success", "cancel_url": f"{base_url}/billing?status=cancelled",
              "client_reference_id": user["id"], "subscription_data": {"metadata": {"user_id": user["id"]}},
              "metadata": {"user_id": user["id"], "plan": plan}, "allow_promotion_codes": True,
              "billing_address_collection": "auto"}
    if env("STRIPE_AUTOMATIC_TAX", "0") == "1":
        params["automatic_tax"] = {"enabled": True}
    if user.get("stripe_customer"):
        params["customer"] = user["stripe_customer"]
    else:
        params["customer_email"] = user["email"]
    idem = f"co-{user['id']}-{plan}-{int(time.time() // 600)}"
    return stripe(secret, "POST", "checkout/sessions", params, idem)["url"]


def portal_url(secret: str, base_url: str, customer: str) -> str:
    return stripe(secret, "POST", "billing_portal/sessions", {"customer": customer, "return_url": f"{base_url}/billing"})["url"]


def verify_signature(payload: bytes, header: str, secret: str, tolerance: int = 300) -> dict:
    """Stripe-Signature: t=<ts>,v1=<hex>[,v1=...]. HMAC-SHA256 over "<t>.<raw body>"."""
    if not secret:
        raise BillingError("Webhook secret not configured")
    parts: dict[str, list[str]] = {}
    for item in (header or "").split(","):
        if "=" in item:
            k, v = item.split("=", 1)
            parts.setdefault(k.strip(), []).append(v.strip())
    try:
        ts = int(parts["t"][0])
    except (KeyError, ValueError, IndexError) as exc:
        raise BillingError("Malformed signature header") from exc
    if abs(time.time() - ts) > tolerance:
        raise BillingError("Signature timestamp outside tolerance (replay?)")
    expected = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, sig) for sig in parts.get("v1", [])):
        raise BillingError("Invalid signature")
    return json.loads(payload)


def _upsert_sub(db: DB, user_id: str, sub: dict) -> None:
    items = sub.get("items", {}).get("data", [])
    pid = items[0]["price"]["id"] if items else ""
    period_end = sub.get("current_period_end") or (items[0].get("current_period_end") if items else None) or 0
    db.run("""INSERT INTO subscriptions VALUES (?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET
              stripe_subscription=excluded.stripe_subscription, plan=excluded.plan, status=excluded.status,
              current_period_end=excluded.current_period_end, updated=excluded.updated""",
           (user_id, sub["id"], plan_for_price(pid) or "unknown", sub.get("status", ""), float(period_end), now()))


def handle_event(db: DB, event: dict) -> str:
    eid = event.get("id", "")
    if db.one("SELECT 1 AS x FROM stripe_events WHERE id=?", (eid,)):
        return "duplicate"
    obj = event.get("data", {}).get("object", {})
    etype = event.get("type", "")
    if etype == "checkout.session.completed" and obj.get("mode") == "subscription":
        uid = obj.get("client_reference_id") or obj.get("metadata", {}).get("user_id")
        if uid and db.one("SELECT id FROM users WHERE id=?", (uid,)):
            db.run("UPDATE users SET stripe_customer=? WHERE id=? AND (stripe_customer IS NULL OR stripe_customer=?)",
                   (obj.get("customer"), uid, obj.get("customer")))
    elif etype.startswith("customer.subscription."):
        # metadata.user_id was written by OUR server at checkout and the event is Stripe-signed -> trustworthy;
        # events may arrive before checkout.session.completed, so bind the customer here too.
        cust, uid = obj.get("customer"), obj.get("metadata", {}).get("user_id")
        user = db.one("SELECT id, stripe_customer FROM users WHERE id=?", (uid,)) if uid else None
        if user and user["stripe_customer"] in (None, cust):
            db.run("UPDATE users SET stripe_customer=? WHERE id=?", (cust, user["id"]))
        else:
            user = db.one("SELECT id FROM users WHERE stripe_customer=?", (cust,))
        if user:
            _upsert_sub(db, user["id"], obj)
    db.run("INSERT INTO stripe_events VALUES (?,?,?)", (eid, etype, now()))
    return "ok"


def entitlement(db: DB, user_id: str) -> dict | None:
    s = db.one("SELECT * FROM subscriptions WHERE user_id=?", (user_id,))
    if s and s["status"] in ACTIVE and (s["current_period_end"] or 0) > now() and s["plan"] in PLANS:
        label, price, _, interval, per_day, max_min = PLANS[s["plan"]]
        return {**s, "label": label, "price": price, "interval": interval, "per_day": per_day, "max_minutes": max_min}
    return None
