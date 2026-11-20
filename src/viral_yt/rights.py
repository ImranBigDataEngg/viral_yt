"""Gate local media processing and publishing on recorded permissions."""
from __future__ import annotations

from pathlib import Path
from .common import ROOT, local_file, read_csv

APPROVED_LICENSES = {"original", "licensed", "public_domain", "cc_by"}
# House rule (EzzyClips Ultra playbook, not a YouTube number): your own narration or
# graphics must cover at least half of any Short built on third-party footage.
MIN_COMMENTARY_SHARE = 0.5
DEFAULT_LEDGER = ROOT / "media" / "authorized" / "assets.csv"


class RightsError(ValueError):
    pass


def is_true(value: str | bool | None) -> bool:
    return value is True or str(value).strip().lower() in ("true", "yes", "1")


def load_asset(asset_id: str, ledger: Path = DEFAULT_LEDGER) -> dict:
    if not ledger.exists():
        raise RightsError(f"Asset ledger absent: {ledger}. Copy examples/assets_template.csv first.")
    matches = [x for x in read_csv(ledger) if x.get("asset_id") == asset_id]
    if len(matches) != 1:
        raise RightsError(f"Expected exactly one asset '{asset_id}' in {ledger}; found {len(matches)}")
    return matches[0]


def validate_asset(asset: dict, require_publish: bool = False) -> tuple[Path, float, float]:
    if not asset.get("asset_id") or not asset.get("source_file"):
        raise RightsError("asset_id and source_file required")
    basis = (asset.get("rights_basis") or "").strip().lower()
    if basis not in APPROVED_LICENSES:
        raise RightsError(f"Invalid rights basis '{basis}'. Require owned/licensed, CC BY or public domain evidence.")
    if len((asset.get("rights_evidence") or "").strip()) < 8:
        raise RightsError("Rights evidence is required (creator or license document / link).")
    if basis != "original" and not (asset.get("commentary_audio") or "").strip():
        raise RightsError("Third-party or licensed footage requires your own commentary_audio, not a bare repost.")
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
    if not (0 <= start < end and 6 <= end - start <= 180):
        raise RightsError("Clip must last 6..180 seconds with nonnegative start and end > start")
    series_key = (asset.get("series") or "").strip()
    if series_key:
        from .series import get_series
        try:
            get_series(series_key)
        except ValueError as exc:
            raise RightsError(str(exc)) from exc
    for media_key in ("transcript_file", "commentary_audio"):
        value = (asset.get(media_key) or "").strip()
        if value and not local_file(value).is_file():
            raise RightsError(f"{media_key} missing: {local_file(value)}")
    return path, start, end


def check_originality(asset: dict, clip_seconds: float, commentary_seconds: float) -> None:
    """Gate against bare re-uploads, which YouTube down-ranks in Shorts since Oct 1, 2026."""
    if (asset.get("rights_basis") or "").strip().lower() == "original":
        return
    share = commentary_seconds / max(clip_seconds, 0.001)
    if share < MIN_COMMENTARY_SHARE:
        raise RightsError(
            f"Commentary covers {share:.0%} of the Short; needs at least {MIN_COMMENTARY_SHARE:.0%}. "
            "Add more breakdown narration or trim the borrowed footage."
        )
