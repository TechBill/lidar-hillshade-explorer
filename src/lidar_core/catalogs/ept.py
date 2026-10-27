"""HoBu/AWS EPT availability catalog."""

from __future__ import annotations

import json
import ssl
import time
from typing import Any, Dict, List, Tuple
from urllib.request import urlopen

try:
    import certifi
    HAS_CERTIFI = True
except ImportError:
    HAS_CERTIFI = False

from utils.config import get_cache_dir, load_config
from lidar_core.catalogs.usgs import (
    LogFunc,
    _bbox_intersects,
    _extract_dataset_id,
    _geometry_covers_bbox,
    _geometry_intersects_bbox,
    _log,
)
from lidar_core.ranking import (
    _enrich_dataset_properties,
    _metadata_for_dataset,
    _normalize_dataset_name,
)

AWS_INDEX_URL = (
    "https://raw.githubusercontent.com/hobu/usgs-lidar/master/"
    "boundaries/resources.geojson"
)
CACHE_EXPIRY_DAYS = 30


def ensure_aws_index(log: LogFunc = None) -> Dict[str, Any]:
    """
    Ensure a local copy of the AWS/USGS LiDAR coverage GeoJSON exists.

    Auto-updates if cached copy is older than CACHE_EXPIRY_DAYS.
    Falls back to cached version if download fails.

    Returns:
        Parsed GeoJSON dictionary
    """
    cache_dir = get_cache_dir()
    index_path = cache_dir / "aws_resources.geojson"

    config = load_config()
    cache_expiry_days = config.get("aws_renewal_days", CACHE_EXPIRY_DAYS)

    needs_update = False

    if index_path.is_file():
        # Check age of cached file
        try:
            file_age_seconds = time.time() - index_path.stat().st_mtime
            age_days = file_age_seconds / 86400

            if age_days < cache_expiry_days:
                _log(log, f"Using cached coverage index (age: {age_days:.1f} days)")
                with index_path.open("r", encoding="utf-8") as f:
                    return json.load(f)
            else:
                _log(log, f"Cached index is {age_days:.1f} days old, updating...")
                needs_update = True
        except Exception as e:
            _log(log, f"Error checking cache age ({e}), using existing file")
            with index_path.open("r", encoding="utf-8") as f:
                return json.load(f)

    # Download fresh copy
    try:
        _log(log, f"Downloading coverage index from GitHub...")

        # Create SSL context with certifi certificates if available
        if HAS_CERTIFI:
            ssl_context = ssl.create_default_context(cafile=certifi.where())
        else:
            ssl_context = ssl.create_default_context()

        with urlopen(AWS_INDEX_URL, timeout=30, context=ssl_context) as resp:
            data = resp.read().decode("utf-8")

        with index_path.open("w", encoding="utf-8") as f:
            f.write(data)

        _log(log, f"Successfully downloaded and cached index")
        return json.loads(data)

    except Exception as e:
        # Download failed - use cached version if available
        if needs_update and index_path.is_file():
            _log(log, f"Download failed ({e}), using cached version")
            with index_path.open("r", encoding="utf-8") as f:
                return json.load(f)
        else:
            raise RuntimeError(
                f"Failed to download AWS LiDAR index and no cache available: {e}"
            ) from e


def _bbox_from_geometry(geom: Dict[str, Any]) -> Tuple[float, float, float, float]:
    """Compute bounding box from GeoJSON geometry."""
    def _walk(c):
        if isinstance(c, (list, tuple)):
            for item in c:
                if isinstance(item, (list, tuple)) and len(item) == 2 and all(
                    isinstance(v, (int, float)) for v in item
                ):
                    yield float(item[0]), float(item[1])
                else:
                    yield from _walk(item)

    coords = geom.get("coordinates")
    xs: List[float] = []
    ys: List[float] = []
    for x, y in _walk(coords):
        xs.append(x)
        ys.append(y)

    if not xs or not ys:
        raise ValueError("Geometry has no coordinate points")

    return (min(xs), min(ys), max(xs), max(ys))


def _get_ept_srs_epsg(ept_url: str) -> int:
    """
    Fetch EPT JSON and infer horizontal EPSG code.
    Defaults to 3857 (Web Mercator) if cannot determine.
    """
    try:
        # Create SSL context with certifi certificates if available
        if HAS_CERTIFI:
            ssl_context = ssl.create_default_context(cafile=certifi.where())
        else:
            ssl_context = ssl.create_default_context()

        with urlopen(ept_url, timeout=10, context=ssl_context) as resp:
            data = json.load(resp)

        srs = data.get("srs") or {}
        horiz = srs.get("horizontal")
        code = horiz.get("code") if isinstance(horiz, dict) else horiz
        if isinstance(code, int):
            return code
        if isinstance(code, str) and code.isdigit():
            return int(code)
    except Exception:
        pass

    return 3857


def _ept_candidates(
    bbox4326: Tuple[float, float, float, float],
    wesm_records: Dict[str, Dict[str, str]],
    log: LogFunc = None,
) -> Dict[str, Dict[str, Any]]:
    """Return intersecting HoBu EPT entries keyed by WESM work unit."""
    index = ensure_aws_index(log=log)
    candidates: Dict[str, Dict[str, Any]] = {}
    for feature in index.get("features", []):
        geom = feature.get("geometry") or {}
        props = feature.get("properties") or {}
        try:
            feature_bbox = feature.get("bbox") or _bbox_from_geometry(geom)
            feature_bbox = tuple(float(value) for value in feature_bbox[:4])
        except Exception:
            continue
        if not _bbox_intersects(bbox4326, feature_bbox):
            continue
        if not _geometry_intersects_bbox(geom, bbox4326):
            continue
        dataset_id = _extract_dataset_id(props)
        if not dataset_id:
            continue
        record = _metadata_for_dataset(dataset_id, wesm_records)
        workunit = (record or {}).get("workunit") or dataset_id
        key = _normalize_dataset_name(workunit)
        enriched = _enrich_dataset_properties(dataset_id, props, wesm_records)
        enriched.update(
            {
                "id": workunit,
                "ept_dataset_id": dataset_id,
                "ept_url": (
                    "https://s3-us-west-2.amazonaws.com/usgs-lidar-public/"
                    f"{dataset_id}/ept.json"
                ),
                "source_type": "ept",
                "covers_aoi": _geometry_covers_bbox(geom, bbox4326),
            }
        )
        candidates[key] = {"id": workunit, "props": enriched}
    return candidates
