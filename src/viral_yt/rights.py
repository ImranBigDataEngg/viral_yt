"""Gate local media processing and publishing on recorded permissions (the rights ledger)."""
from __future__ import annotations

from pathlib import Path

from .common import ROOT, is_true, local_file, read_csv

# original      : you recorded/created it (incl. your own gameplay where the publisher's video policy allows it)
# licensed      : written licence / contract / creator clipping-programme terms that allow edit + monetise + cross-post
# permission    : explicit written permission from the rights holder (email/DM screenshot) for this use
# cc_by         : YouTube "Creative Commons – Attribution" (or CC BY elsewhere); credit is mandatory
# public_domain : e.g. most NASA footage; check each item's notes
APPROVED_LICENSES = {"original", "licensed", "permission", "public_domain", "cc_by"}
THIRD_PARTY = APPROVED_LICENSES - {"original"}
DEFAULT_LEDGER = ROOT / "media" / "authorized" / "assets.csv"


class RightsError(ValueError):
    pass


def load_ledger(ledger: Path = DEFAULT_LEDGER) -> list[dict]:
    if not ledger.exists():
        raise RightsError(f"Asset ledger absent: {ledger}. Copy examples/assets_template.csv first.")
    return read_csv(ledger)


def load_asset(asset_id: str, ledger: Path = DEFAULT_LEDGER) -> dict:
    matches = [x for x in load_ledger(ledger) if x.get("asset_id") == asset_id]
    if len(matches) != 1:
        raise RightsError(f"Expected exactly one asset '{asset_id}' in {ledger}; found {len(matches)}")
    return matches[0]


def parse_ranges(value: str) -> list[tuple[float, float]]:
    out = []
    for part in (value or "").replace(",", ";").split(";"):
        part = part.strip()
        if not part or part.lower() == "auto":
            continue
        a, b = part.split("-", 1)
        out.append((float(a), float(b)))
    return out


def validate_asset(asset: dict, require_publish: bool = False) -> tuple[Path, float, float]:
    if not asset.get("asset_id") or not asset.get("source_file"):
        raise RightsError("asset_id and source_file required")
    basis = (asset.get("rights_basis") or "").strip().lower()
    if basis not in APPROVED_LICENSES:
        raise RightsError(f"Invalid rights basis '{basis}'. Allowed: {sorted(APPROVED_LICENSES)} with evidence.")
    if len((asset.get("rights_evidence") or "").strip()) < 8:
        raise RightsError("Rights evidence is required (licence link, contract, permission screenshot path...).")
    if basis in THIRD_PARTY and not (asset.get("commentary_audio") or "").strip():
        raise RightsError("Third-party footage requires your own commentary_audio (voice-over), not a bare repost.")
    if basis in THIRD_PARTY and not (asset.get("owner") or "").strip():
        raise RightsError("Third-party footage requires 'owner' for the on-screen/description credit.")
    if not is_true(asset.get("approved")):
        raise RightsError("This asset has not been approved for processing.")
    if require_publish and not is_true(asset.get("publish_approved")):
        raise RightsError("Public release requires publish_approved=true in the asset ledger.")
    path = local_file(asset["source_file"])
    if not path.is_file():
        raise RightsError(f"Local authorized source file not found: {path}")
    try:
        start = float(asset.get("clip_start") or 0)
        end = float(asset.get("clip_end") or 0)
    except ValueError as exc:
        raise RightsError("Clip boundaries must be numeric seconds") from exc
    ranges = parse_ranges(asset.get("clip_ranges", ""))
    auto = (asset.get("clip_ranges") or "").strip().lower() == "auto"
    if not ranges and not auto and not (0 <= start < end and 6 <= end - start <= 180):
        raise RightsError("Clip must last 6..180 seconds with nonnegative start and end > start")
    for a, b in ranges:
        if not (0 <= a < b and 6 <= b - a <= 180):
            raise RightsError(f"clip_ranges entry {a}-{b} must last 6..180 s")
    if auto and not (asset.get("transcript_file") or "").strip():
        import importlib.util
        if importlib.util.find_spec("faster_whisper") is None:
            raise RightsError("clip_ranges=auto needs transcript_file, or `pip install faster-whisper` for automatic transcription")
    for key in ("transcript_file", "commentary_audio"):
        value = (asset.get(key) or "").strip()
        if value and not local_file(value).is_file():
            raise RightsError(f"{key} missing: {local_file(value)}")
    return path, start, end
