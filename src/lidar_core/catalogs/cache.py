"""Small, short-lived JSON cache for remote catalog discovery responses."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Optional, Tuple

from utils.config import get_cache_dir, load_config


DEFAULT_CATALOG_CACHE_MINUTES = 30


def _cache_lifetime_seconds() -> float:
    value = load_config().get(
        "catalog_cache_minutes", DEFAULT_CATALOG_CACHE_MINUTES
    )
    try:
        minutes = float(value)
    except (TypeError, ValueError):
        minutes = DEFAULT_CATALOG_CACHE_MINUTES
    return max(1.0, min(24 * 60.0, minutes)) * 60.0


def _cache_path(
    provider: str,
    bbox4326: Tuple[float, float, float, float],
) -> Path:
    rounded = ",".join(f"{float(value):.5f}" for value in bbox4326)
    digest = hashlib.sha256(f"{provider}:{rounded}".encode("utf-8")).hexdigest()[:20]
    directory = get_cache_dir() / "catalog_cache"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{provider}_{digest}.json"


def load_catalog_cache(
    provider: str,
    bbox4326: Tuple[float, float, float, float],
) -> Optional[Any]:
    """Return a fresh cached JSON value, or None on miss/corruption."""
    path = _cache_path(provider, bbox4326)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        created = float(payload["created"])
        if time.time() - created > _cache_lifetime_seconds():
            return None
        return payload["value"]
    except (OSError, TypeError, ValueError, KeyError):
        return None


def load_stale_catalog_cache(
    provider: str,
    bbox4326: Tuple[float, float, float, float],
    max_age_hours: float = 24.0,
) -> Optional[Any]:
    """Return a recent stale value for graceful provider-outage fallback."""
    path = _cache_path(provider, bbox4326)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        created = float(payload["created"])
        if time.time() - created > max(1.0, max_age_hours) * 3600.0:
            return None
        return payload["value"]
    except (OSError, TypeError, ValueError, KeyError):
        return None


def save_catalog_cache(
    provider: str,
    bbox4326: Tuple[float, float, float, float],
    value: Any,
) -> None:
    """Atomically save a JSON-serializable provider response."""
    path = _cache_path(provider, bbox4326)
    temporary = path.with_suffix(".tmp")
    payload = {"created": time.time(), "value": value}
    try:
        temporary.write_text(json.dumps(payload), encoding="utf-8")
        temporary.replace(path)
    except (OSError, TypeError, ValueError):
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
