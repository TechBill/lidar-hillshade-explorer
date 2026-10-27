#!/usr/bin/env python3
"""
AWS/USGS LiDAR operations for LiDAR Hillshade Explorer.

Handles USGS/OpenTopography discovery, selection, and download/clipping.
Combined functionality from LidarStudio's aws_index, aws_select_dataset, and aws_clip_class2.
"""

from __future__ import annotations

import csv
import io
import json
import math
import re
import subprocess
import tempfile
import time
import ssl
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple, Optional
from urllib.parse import unquote, urlencode, urlparse
from urllib.request import Request
from urllib.request import urlopen

try:
    import certifi
    HAS_CERTIFI = True
except ImportError:
    HAS_CERTIFI = False

from utils.config import get_cache_dir, load_config, get_output_dir
from utils.subprocess_utils import get_creation_flags, get_startupinfo


AWS_INDEX_URL = (
    "https://raw.githubusercontent.com/hobu/usgs-lidar/master/"
    "boundaries/resources.geojson"
)
WESM_URL = (
    "https://prd-tnm.s3.amazonaws.com/"
    "StagedProducts/Elevation/metadata/WESM.csv"
)
TNM_PRODUCTS_URL = "https://tnmaccess.nationalmap.gov/api/v1/products"
TNM_OPR_DATASET = "Original Product Resolution (OPR) Digital Elevation Model (DEM)"
TNM_LPC_DATASET = "Lidar Point Cloud (LPC)"
OPENTOPO_CATALOG_URL = "https://portal.opentopography.org/API/otCatalog"
OPENTOPO_STAC_BASE_URL = "https://portal.opentopography.org/stac"
OPENTOPO_DATA_HOST = "opentopography.s3.sdsc.edu"

CACHE_EXPIRY_DAYS = 30
WESM_CACHE_EXPIRY_DAYS = 7
MIN_WESM_RECORDS = 1000
REQUIRED_WESM_FIELDS = {
    "workunit",
    "collect_start",
    "collect_end",
    "ql",
    "dem_gsd_meters",
    "horiz_crs",
    "vert_crs",
    "geoid",
    "lpc_pub_date",
    "lpc_category",
    "sourcedem_pub_date",
    "sourcedem_category",
    "lpc_link",
    "sourcedem_link",
    "metadata_link",
}
MIN_LAZ_SIZE = 2000  # Minimum LAZ file size in bytes
MIN_POINTS_PER_SQ_MI = 100000  # Minimum point density

LogFunc = Optional[callable]


def _log(log: LogFunc, msg: str) -> None:
    """Helper to log message if logger provided."""
    if log is not None:
        log(msg)


# ============================================================================
# AWS Index Management
# ============================================================================

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


def _parse_wesm_csv(csv_text: str) -> Dict[str, Dict[str, str]]:
    """Parse WESM records into an index keyed by normalized work-unit name."""
    records: Dict[str, Dict[str, str]] = {}
    for row in csv.DictReader(io.StringIO(csv_text)):
        workunit = (row.get("workunit") or "").strip()
        if workunit:
            records[_normalize_dataset_name(workunit)] = dict(row)
    return records


def _validate_wesm_records(records: Dict[str, Dict[str, str]]) -> None:
    """Reject malformed or suspiciously incomplete nationwide WESM exports."""
    if len(records) < MIN_WESM_RECORDS:
        raise ValueError(
            f"USGS metadata file contained only {len(records):,} work units; "
            f"expected at least {MIN_WESM_RECORDS:,}"
        )

    sample = next(iter(records.values()))
    missing_fields = REQUIRED_WESM_FIELDS.difference(sample)
    if missing_fields:
        missing = ", ".join(sorted(missing_fields))
        raise ValueError(f"USGS metadata file is missing required fields: {missing}")


def ensure_wesm_metadata(log: LogFunc = None) -> Dict[str, Dict[str, str]]:
    """Return cached authoritative USGS work-unit metadata when available."""
    cache_path = get_cache_dir() / "WESM.csv"
    config = load_config()
    try:
        expiry_days = max(1, int(config.get("wesm_renewal_days", WESM_CACHE_EXPIRY_DAYS)))
    except (TypeError, ValueError):
        expiry_days = WESM_CACHE_EXPIRY_DAYS

    cache_is_fresh = False
    if cache_path.is_file():
        age_days = (time.time() - cache_path.stat().st_mtime) / 86400
        cache_is_fresh = age_days < expiry_days
        if cache_is_fresh:
            try:
                records = _parse_wesm_csv(cache_path.read_text(encoding="utf-8-sig"))
                _validate_wesm_records(records)
                _log(log, f"Using cached USGS metadata (age: {age_days:.1f} days)")
                return records
            except Exception as exc:
                _log(log, f"Cached USGS metadata is invalid ({exc}); refreshing...")

    try:
        _log(log, "Downloading authoritative USGS acquisition metadata...")
        if HAS_CERTIFI:
            ssl_context = ssl.create_default_context(cafile=certifi.where())
        else:
            ssl_context = ssl.create_default_context()
        with urlopen(WESM_URL, timeout=30, context=ssl_context) as response:
            csv_text = response.read().decode("utf-8-sig")

        records = _parse_wesm_csv(csv_text)
        _validate_wesm_records(records)

        temp_path = cache_path.with_suffix(".csv.tmp")
        temp_path.write_text(csv_text, encoding="utf-8")
        temp_path.replace(cache_path)
        _log(log, f"Cached metadata for {len(records):,} USGS work units")
        return records
    except Exception as exc:
        if cache_path.is_file():
            try:
                records = _parse_wesm_csv(cache_path.read_text(encoding="utf-8-sig"))
                _validate_wesm_records(records)
                _log(log, f"USGS metadata refresh failed ({exc}); using cached copy")
                return records
            except Exception:
                pass
        _log(
            log,
            f"USGS acquisition metadata unavailable ({exc}); using estimated years",
        )
        return {}


def _bbox_intersects(a: Tuple[float, float, float, float],
                     b: Tuple[float, float, float, float]) -> bool:
    """Check if two bounding boxes intersect."""
    a_minx, a_miny, a_maxx, a_maxy = a
    b_minx, b_miny, b_maxx, b_maxy = b
    if a_maxx < b_minx or a_minx > b_maxx:
        return False
    if a_maxy < b_miny or a_miny > b_maxy:
        return False
    return True


def _point_in_bbox(
    point: Tuple[float, float],
    bbox: Tuple[float, float, float, float],
) -> bool:
    """Return whether a point is inside or on an axis-aligned bbox."""
    x, y = point
    minx, miny, maxx, maxy = bbox
    return minx <= x <= maxx and miny <= y <= maxy


def _orientation(
    a: Tuple[float, float],
    b: Tuple[float, float],
    c: Tuple[float, float],
) -> float:
    """Return the signed orientation of three points."""
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _point_on_segment(
    point: Tuple[float, float],
    start: Tuple[float, float],
    end: Tuple[float, float],
) -> bool:
    """Return whether a point lies on a closed line segment."""
    epsilon = 1e-12
    if abs(_orientation(start, end, point)) > epsilon:
        return False
    return (
        min(start[0], end[0]) - epsilon <= point[0] <= max(start[0], end[0]) + epsilon
        and min(start[1], end[1]) - epsilon <= point[1] <= max(start[1], end[1]) + epsilon
    )


def _segments_intersect(
    a_start: Tuple[float, float],
    a_end: Tuple[float, float],
    b_start: Tuple[float, float],
    b_end: Tuple[float, float],
) -> bool:
    """Return whether two closed line segments intersect."""
    a1 = _orientation(a_start, a_end, b_start)
    a2 = _orientation(a_start, a_end, b_end)
    b1 = _orientation(b_start, b_end, a_start)
    b2 = _orientation(b_start, b_end, a_end)

    if ((a1 > 0 and a2 < 0) or (a1 < 0 and a2 > 0)) and (
        (b1 > 0 and b2 < 0) or (b1 < 0 and b2 > 0)
    ):
        return True

    return (
        (abs(a1) <= 1e-12 and _point_on_segment(b_start, a_start, a_end))
        or (abs(a2) <= 1e-12 and _point_on_segment(b_end, a_start, a_end))
        or (abs(b1) <= 1e-12 and _point_on_segment(a_start, b_start, b_end))
        or (abs(b2) <= 1e-12 and _point_on_segment(a_end, b_start, b_end))
    )


def _ring_points(ring: Any) -> List[Tuple[float, float]]:
    """Return valid two-dimensional points from a GeoJSON linear ring."""
    points: List[Tuple[float, float]] = []
    if not isinstance(ring, (list, tuple)):
        return points
    for coordinate in ring:
        if (
            isinstance(coordinate, (list, tuple))
            and len(coordinate) >= 2
            and isinstance(coordinate[0], (int, float))
            and isinstance(coordinate[1], (int, float))
        ):
            points.append((float(coordinate[0]), float(coordinate[1])))
    return points


def _point_in_ring(point: Tuple[float, float], ring: Any) -> bool:
    """Return whether a point is inside or on a GeoJSON linear ring."""
    points = _ring_points(ring)
    if len(points) < 3:
        return False

    inside = False
    previous = points[-1]
    for current in points:
        if _point_on_segment(point, previous, current):
            return True
        if (current[1] > point[1]) != (previous[1] > point[1]):
            crossing_x = (
                (previous[0] - current[0])
                * (point[1] - current[1])
                / (previous[1] - current[1])
                + current[0]
            )
            if point[0] < crossing_x:
                inside = not inside
        previous = current
    return inside


def _ring_intersects_bbox(
    ring: Any,
    bbox: Tuple[float, float, float, float],
) -> bool:
    """Return whether a GeoJSON ring boundary intersects a bbox."""
    points = _ring_points(ring)
    if len(points) < 2:
        return False
    if any(_point_in_bbox(point, bbox) for point in points):
        return True

    minx, miny, maxx, maxy = bbox
    corners = (
        (minx, miny),
        (maxx, miny),
        (maxx, maxy),
        (minx, maxy),
    )
    bbox_edges = tuple(zip(corners, corners[1:] + corners[:1]))
    previous = points[-1]
    for current in points:
        if any(
            _segments_intersect(previous, current, edge_start, edge_end)
            for edge_start, edge_end in bbox_edges
        ):
            return True
        previous = current
    return False


def _polygon_intersects_bbox(
    polygon: Any,
    bbox: Tuple[float, float, float, float],
) -> bool:
    """Return whether a GeoJSON polygon intersects a bbox, respecting holes."""
    if not isinstance(polygon, (list, tuple)) or not polygon:
        return False

    outer_ring = polygon[0]
    if _ring_intersects_bbox(outer_ring, bbox):
        return True

    minx, miny, maxx, maxy = bbox
    for corner in ((minx, miny), (maxx, miny), (maxx, maxy), (minx, maxy)):
        if _point_in_ring(corner, outer_ring) and not any(
            _point_in_ring(corner, hole) for hole in polygon[1:]
        ):
            return True
    return False


def _geometry_intersects_bbox(
    geom: Dict[str, Any],
    bbox: Tuple[float, float, float, float],
) -> bool:
    """Return whether a GeoJSON Polygon or MultiPolygon intersects a bbox."""
    geometry_type = geom.get("type")
    coordinates = geom.get("coordinates")
    if geometry_type == "Polygon":
        return _polygon_intersects_bbox(coordinates, bbox)
    if geometry_type == "MultiPolygon" and isinstance(coordinates, (list, tuple)):
        return any(_polygon_intersects_bbox(polygon, bbox) for polygon in coordinates)
    return False


def _extract_dataset_id(props: Dict[str, Any]) -> Optional[str]:
    """Extract dataset ID from properties."""
    for key in ("id", "ID", "Id", "name", "Name", "title", "Title"):
        if key in props and isinstance(props[key], str):
            v = props[key].strip()
            if v:
                return v
    return None


def _normalize_dataset_name(name: str) -> str:
    """Normalize a dataset/work-unit identifier for exact metadata matching."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _wesm_lookup_keys(dataset_id: str) -> List[str]:
    """Return conservative aliases used by older USGS EPT dataset names."""
    names = {dataset_id.strip()}
    stripped = re.sub(r"^USGS_LPC_", "", dataset_id, flags=re.IGNORECASE)
    stripped = re.sub(r"_LAS_\d{4}$", "", stripped, flags=re.IGNORECASE)
    names.add(stripped)
    return [_normalize_dataset_name(name) for name in names if name]


def _quality_level(value: str) -> Optional[int]:
    """Extract the numeric quality level from a WESM value such as 'QL 1'."""
    match = re.search(r"\bQL\s*(\d+)\b", value or "", flags=re.IGNORECASE)
    return int(match.group(1)) if match else None


def _metadata_for_dataset(
    dataset_id: str, wesm_records: Dict[str, Dict[str, str]]
) -> Optional[Dict[str, str]]:
    for key in _wesm_lookup_keys(dataset_id):
        record = wesm_records.get(key)
        if record:
            return record
    return None


def _enrich_dataset_properties(
    dataset_id: str,
    props: Dict[str, Any],
    wesm_records: Dict[str, Dict[str, str]],
) -> Dict[str, Any]:
    """Attach authoritative collection metadata or a marked estimate."""
    enriched = dict(props)
    record = _metadata_for_dataset(dataset_id, wesm_records)
    if record:
        collect_start = (record.get("collect_start") or "").replace("/", "-")
        collect_end = (record.get("collect_end") or "").replace("/", "-")
        ql = _quality_level(record.get("ql") or "")
        year_text = collect_end[:4] or collect_start[:4]
        enriched.update(
            {
                "workunit": record.get("workunit") or dataset_id,
                "collection_start": collect_start or None,
                "collection_end": collect_end or None,
                "collection_year": int(year_text) if year_text.isdigit() else None,
                "quality_level": ql,
                "dem_gsd_meters": record.get("dem_gsd_meters") or None,
                "horizontal_crs": record.get("horiz_crs") or None,
                "vertical_crs": record.get("vert_crs") or None,
                "geoid": record.get("geoid") or None,
                "lpc_publication_date": (record.get("lpc_pub_date") or "").replace("/", "-") or None,
                "lpc_update_date": (record.get("lpc_update") or "").replace("/", "-") or None,
                "lpc_category": record.get("lpc_category") or None,
                "lpc_reason": record.get("lpc_reason") or None,
                "source_dem_publication_date": (record.get("sourcedem_pub_date") or "").replace("/", "-") or None,
                "source_dem_update_date": (record.get("sourcedem_update") or "").replace("/", "-") or None,
                "source_dem_category": record.get("sourcedem_category") or None,
                "source_dem_reason": record.get("sourcedem_reason") or None,
                "lpc_link": record.get("lpc_link") or None,
                "source_dem_link": record.get("sourcedem_link") or None,
                "metadata_link": record.get("metadata_link") or None,
                "year_estimated": False,
                "metadata_source": "USGS WESM",
            }
        )
    else:
        estimated_year = int(_extract_year(props))
        enriched.update(
            {
                "workunit": dataset_id,
                "collection_year": estimated_year if estimated_year > 1900 else None,
                "quality_level": None,
                "year_estimated": True,
                "metadata_source": "dataset name",
            }
        )
    return enriched


def _date_value(value: Any) -> int:
    """Convert an ISO-like date into a sortable YYYYMMDD integer."""
    digits = re.sub(r"[^0-9]", "", str(value or ""))[:8]
    return int(digits.ljust(8, "0")) if digits else 0


def _validation_score(props: Dict[str, Any]) -> int:
    """Rank the validation category for the candidate's selected product."""
    source_type = props.get("source_type")
    if source_type == "source_dem":
        category = props.get("source_dem_category")
    else:
        category = props.get("lpc_category")
    normalized = str(category or "").strip().lower()
    return {
        "meets": 4,
        "meets with variance": 3,
        "expected to meet": 2,
        "under review": 1,
    }.get(normalized, 0)


def _dataset_sort_key(
    dataset_id: str,
    props: Dict[str, Any],
    ranking_mode: str = "best_detail",
) -> tuple:
    """Return a stable Best Detail or Newest Survey ranking key."""
    date_text = str(props.get("collection_end") or "").replace("-", "")
    if len(date_text) == 8 and date_text.isdigit():
        date_value = int(date_text)
    else:
        year = props.get("collection_year")
        date_value = int(year) * 10000 if isinstance(year, int) else 19000000
    quality = props.get("quality_level")
    quality_score = -int(quality) if isinstance(quality, int) else -999
    try:
        resolution_score = -float(props.get("dem_gsd_meters"))
    except (TypeError, ValueError):
        resolution_score = -999.0
    updated = max(
        _date_value(props.get("source_dem_update_date")),
        _date_value(props.get("lpc_update_date")),
        _date_value(props.get("source_dem_publication_date")),
        _date_value(props.get("lpc_publication_date")),
    )
    validation = _validation_score(props)
    coverage = 1 if props.get("covers_aoi", False) else 0
    if ranking_mode == "newest":
        return (
            coverage,
            date_value,
            quality_score,
            resolution_score,
            validation,
            updated,
            dataset_id,
        )
    return (
        coverage,
        resolution_score,
        validation,
        quality_score,
        date_value,
        updated,
        dataset_id,
    )


def _extract_year(props: Dict[str, Any]) -> float:
    """
    Extract dataset year from properties.

    Looks for 4-digit years (1900-2100) in string properties.
    Also decodes codes like 'B24' or 'D22' where digits represent year.
    """
    year_candidates: List[int] = []

    # Look for 4-digit year tokens
    for v in props.values():
        if not isinstance(v, str):
            continue
        text = v.replace("_", " ").replace("-", " ")
        for token in text.split():
            if len(token) == 4 and token.isdigit():
                y = int(token)
                if 1900 <= y <= 2100:
                    year_candidates.append(y)

    if year_candidates:
        return float(max(year_candidates))

    # Look for trailing 2-digit codes like 'D22', 'B24'
    for v in props.values():
        if not isinstance(v, str):
            continue
        v = v.strip()
        if len(v) < 3:
            continue
        tail = v[-3:]
        if tail[0].isalpha() and tail[1:].isdigit():
            yy = int(tail[1:])
            # Bias toward 2000s for modern collections
            if yy >= 90:
                y_full = 1900 + yy
            else:
                y_full = 2000 + yy
            if 1900 <= y_full <= 2100:
                year_candidates.append(y_full)

    if year_candidates:
        return float(max(year_candidates))

    # Fallback
    return 1900.0


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


def _query_tnm_products(
    dataset: str,
    bbox4326: Tuple[float, float, float, float],
    log: LogFunc = None,
) -> List[Dict[str, Any]]:
    """Query official TNM products intersecting an AOI, following pagination."""
    bbox_text = ",".join(f"{float(value):.10f}" for value in bbox4326)
    products: List[Dict[str, Any]] = []
    offset = 0
    page_size = 1000

    while True:
        query = urlencode(
            {
                "bbox": bbox_text,
                "datasets": dataset,
                "max": page_size,
                "offset": offset,
            }
        )
        request = Request(
            f"{TNM_PRODUCTS_URL}?{query}",
            headers={"User-Agent": "LiDAR-Hillshade-Explorer/6.3.1"},
        )
        if HAS_CERTIFI:
            ssl_context = ssl.create_default_context(cafile=certifi.where())
        else:
            ssl_context = ssl.create_default_context()
        with urlopen(request, timeout=45, context=ssl_context) as response:
            payload = json.load(response)

        errors = payload.get("errors") or []
        if errors:
            raise RuntimeError("; ".join(str(error) for error in errors))
        page = payload.get("items") or []
        products.extend(item for item in page if isinstance(item, dict))
        total = int(payload.get("total") or len(products))
        if len(products) >= total or not page:
            break
        if len(products) >= 5000:
            raise RuntimeError(
                "The requested area intersects more than 5,000 USGS product tiles; "
                "choose a smaller area."
            )
        offset += len(page)

    _log(log, f"USGS TNM returned {len(products):,} {dataset} tile(s)")
    return products


def _product_download_url(product: Dict[str, Any]) -> Optional[str]:
    """Return the preferred HTTPS URL from a TNM product record."""
    candidates = [product.get("downloadLazURL"), product.get("downloadURL")]
    urls = product.get("urls")
    if isinstance(urls, dict):
        candidates.extend(urls.values())
    for value in candidates:
        if isinstance(value, str) and value.startswith("https://"):
            return value
    return None


def _product_workunit_key(
    product: Dict[str, Any],
    wesm_records: Dict[str, Dict[str, str]],
) -> Optional[str]:
    """Resolve a TNM tile to its WESM work unit using official product paths."""
    sources = [
        _product_download_url(product),
        product.get("vendorMetaUrl"),
        product.get("previewGraphicURL"),
    ]
    for source in sources:
        if not isinstance(source, str):
            continue
        parts = [unquote(part) for part in urlparse(source).path.split("/") if part]
        for marker in ("LAZ", "LAS", "TIFF", "IMG", "browse"):
            if marker in parts:
                index = parts.index(marker)
                if index > 0:
                    key = _normalize_dataset_name(parts[index - 1])
                    if key:
                        return key

    searchable = " ".join(
        str(product.get(key) or "")
        for key in ("title", "vendorMetaUrl", "downloadURL", "downloadLazURL")
    ).lower()
    matches = [key for key in wesm_records if key and key in _normalize_dataset_name(searchable)]
    return max(matches, key=len) if matches else None


def _product_bbox(product: Dict[str, Any]) -> Optional[Tuple[float, float, float, float]]:
    bounds = product.get("boundingBox")
    if not isinstance(bounds, dict):
        return None
    try:
        return (
            float(bounds["minX"]),
            float(bounds["minY"]),
            float(bounds["maxX"]),
            float(bounds["maxY"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _products_cover_bbox(
    products: List[Dict[str, Any]],
    bbox: Tuple[float, float, float, float],
) -> bool:
    """Return whether the union of TNM tile extents covers the entire AOI."""
    minx, miny, maxx, maxy = bbox
    rectangles = []
    x_breaks = {minx, maxx}
    for product in products:
        bounds = _product_bbox(product)
        if not bounds:
            continue
        left = max(minx, bounds[0])
        bottom = max(miny, bounds[1])
        right = min(maxx, bounds[2])
        top = min(maxy, bounds[3])
        if left < right and bottom < top:
            rectangles.append((left, bottom, right, top))
            x_breaks.update((left, right))
    if not rectangles:
        return False

    xs = sorted(x_breaks)
    epsilon = 1e-10
    for left, right in zip(xs, xs[1:]):
        if right - left <= epsilon:
            continue
        middle = (left + right) / 2
        intervals = sorted(
            (bottom, top)
            for rect_left, bottom, rect_right, top in rectangles
            if rect_left - epsilon <= middle <= rect_right + epsilon
        )
        covered_to = miny
        for bottom, top in intervals:
            if bottom > covered_to + epsilon:
                break
            covered_to = max(covered_to, top)
            if covered_to >= maxy - epsilon:
                break
        if covered_to < maxy - epsilon:
            return False
    return True


def _geometry_covers_bbox(
    geom: Dict[str, Any], bbox: Tuple[float, float, float, float]
) -> bool:
    """Conservatively sample an EPT footprint for full AOI coverage."""
    polygons = []
    if geom.get("type") == "Polygon":
        polygons = [geom.get("coordinates")]
    elif geom.get("type") == "MultiPolygon":
        polygons = geom.get("coordinates") or []
    if not polygons:
        return False

    minx, miny, maxx, maxy = bbox
    for x_index in range(7):
        x = minx + (maxx - minx) * x_index / 6
        for y_index in range(7):
            y = miny + (maxy - miny) * y_index / 6
            if not any(
                polygon
                and _point_in_ring((x, y), polygon[0])
                and not any(_point_in_ring((x, y), hole) for hole in polygon[1:])
                for polygon in polygons
            ):
                return False
    return True


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


def _fetch_json_url(url: str, timeout: int = 45) -> Dict[str, Any]:
    """Fetch a public JSON document with the app's normal TLS handling."""
    request = Request(
        url,
        headers={"User-Agent": "LiDAR-Hillshade-Explorer/6.3.1"},
    )
    if HAS_CERTIFI:
        ssl_context = ssl.create_default_context(cafile=certifi.where())
    else:
        ssl_context = ssl.create_default_context()
    with urlopen(request, timeout=timeout, context=ssl_context) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise ValueError("Expected a JSON object")
    return payload


def _catalog_property(dataset: Dict[str, Any], name: str) -> Optional[str]:
    """Return a named OpenTopography additionalProperty value."""
    for prop in dataset.get("spatialCoverage", {}).get("additionalProperty", []):
        if isinstance(prop, dict) and prop.get("name") == name:
            value = prop.get("value")
            return str(value).strip() if value is not None else None
    return None


def _catalog_area_sq_km(dataset: Dict[str, Any]) -> Optional[float]:
    """Extract the numeric catalog area, when supplied in square kilometers."""
    measurements = dataset.get("variableMeasured") or dataset.get(
        "spatialCoverage", {}
    ).get("variableMeasured", [])
    for prop in measurements:
        if not isinstance(prop, dict) or prop.get("name") != "Area":
            continue
        match = re.search(r"[0-9]+(?:\.[0-9]+)?", str(prop.get("value") or ""))
        if match:
            return float(match.group(0))
    return None


def _catalog_geometry(dataset: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Extract the first GeoJSON geometry from an OpenTopography record."""
    features = (
        dataset.get("spatialCoverage", {})
        .get("geo", {})
        .get("geojson", {})
        .get("features", [])
    )
    for feature in features:
        geometry = feature.get("geometry") if isinstance(feature, dict) else None
        if isinstance(geometry, dict):
            return geometry
    return None


def _catalog_temporal_dates(value: Any) -> Tuple[Optional[str], Optional[str]]:
    """Normalize an OpenTopography date or date range."""
    parts = [part.strip()[:10] for part in str(value or "").split("/")]
    parts = [part for part in parts if re.fullmatch(r"\d{4}-\d{2}-\d{2}", part)]
    if not parts:
        return None, None
    return parts[0], parts[-1]


def _remote_raster_resolution(url: str) -> float:
    """Read only a public COG's header and return its native pixel size."""
    import rasterio

    with rasterio.Env(GDAL_HTTP_MAX_RETRY="2", GDAL_HTTP_RETRY_DELAY="1"):
        with rasterio.open(url) as source:
            xres, yres = source.res
    resolution = (abs(float(xres)) + abs(float(yres))) / 2.0
    if not math.isfinite(resolution) or resolution <= 0:
        raise ValueError("OpenTopography raster has an invalid pixel size")
    return resolution


def _opentopography_item_for_collection(
    short_name: str,
) -> Tuple[Dict[str, Any], str, Dict[str, Any]]:
    """Return the preferred bare-earth/DTM STAC item and dataset page URL."""
    collection_url = f"{OPENTOPO_STAC_BASE_URL}/{short_name}_collection.json"
    collection = _fetch_json_url(collection_url)
    item_links = [
        link.get("href")
        for link in collection.get("links", [])
        if isinstance(link, dict)
        and link.get("rel") == "item"
        and isinstance(link.get("href"), str)
        and str(link.get("href")).startswith(f"{OPENTOPO_STAC_BASE_URL}/")
    ]
    item_links.sort(
        key=lambda href: any(
            marker in href.lower() for marker in ("_be.", "_dtm.", "bare")
        ),
        reverse=True,
    )
    for item_url in item_links:
        item = _fetch_json_url(item_url)
        title = str(item.get("properties", {}).get("title") or "").lower()
        item_id = str(item.get("id") or "").lower()
        if any(marker in f"{title} {item_id}" for marker in ("dtm", "bare", "_be")):
            dataset_page = next(
                (
                    str(link.get("href"))
                    for link in collection.get("links", [])
                    if isinstance(link, dict) and link.get("rel") == "about"
                ),
                collection_url,
            )
            return item, dataset_page, collection
    raise RuntimeError("No bare-earth or DTM raster item is published")


def _opentopography_candidates(
    bbox4326: Tuple[float, float, float, float],
    log: LogFunc = None,
) -> Dict[str, Dict[str, Any]]:
    """Discover public, localized lidar DTMs from OpenTopography's catalog."""
    minx, miny, maxx, maxy = bbox4326
    query = urlencode(
        {
            "productFormat": "Raster",
            "minx": f"{minx:.10f}",
            "miny": f"{miny:.10f}",
            "maxx": f"{maxx:.10f}",
            "maxy": f"{maxy:.10f}",
            "detail": "true",
            "outputFormat": "json",
            "include_federated": "false",
        }
    )
    payload = _fetch_json_url(f"{OPENTOPO_CATALOG_URL}?{query}")
    results: Dict[str, Dict[str, Any]] = {}

    for wrapper in payload.get("Datasets", []):
        dataset = wrapper.get("Dataset") if isinstance(wrapper, dict) else None
        if not isinstance(dataset, dict):
            continue
        keywords = str(dataset.get("keywords") or "").lower()
        area_sq_km = _catalog_area_sq_km(dataset)
        geometry = _catalog_geometry(dataset)
        short_name = str(dataset.get("alternateName") or "").strip()
        if (
            "lidar" not in keywords
            or not short_name
            or not re.fullmatch(r"[A-Za-z0-9_.-]+", short_name)
            or area_sq_km is None
            or area_sq_km > 250_000
            or not geometry
            or not _geometry_covers_bbox(geometry, bbox4326)
        ):
            continue

        try:
            item, dataset_page, collection = _opentopography_item_for_collection(
                short_name
            )
        except Exception as exc:
            _log(log, f"Skipping OpenTopography {short_name}: {exc}")
            continue

        products: List[Dict[str, Any]] = []
        for name, asset in item.get("assets", {}).items():
            if not isinstance(asset, dict):
                continue
            url = asset.get("href")
            bounds = asset.get("bbox")
            roles = asset.get("roles") or []
            media_type = str(asset.get("type") or "").lower()
            if (
                not isinstance(url, str)
                or not url.startswith(f"https://{OPENTOPO_DATA_HOST}/")
                or "data" not in roles
                or "tiff" not in media_type
                or not isinstance(bounds, list)
                or len(bounds) < 4
            ):
                continue
            try:
                asset_bbox = tuple(float(value) for value in bounds[:4])
            except (TypeError, ValueError):
                continue
            if not _bbox_intersects(asset_bbox, bbox4326):
                continue
            products.append(
                {
                    "title": name,
                    "downloadURL": url,
                    "sizeInBytes": int(asset.get("file:size") or 0),
                    "boundingBox": {
                        "minX": asset_bbox[0],
                        "minY": asset_bbox[1],
                        "maxX": asset_bbox[2],
                        "maxY": asset_bbox[3],
                    },
                }
            )
        if not products or not _products_cover_bbox(products, bbox4326):
            continue

        try:
            resolution = _remote_raster_resolution(
                str(products[0]["downloadURL"])
            )
        except Exception as exc:
            _log(
                log,
                f"Skipping OpenTopography {short_name}: "
                f"cannot read resolution ({exc})",
            )
            continue

        identifier = dataset.get("identifier") or {}
        opentopo_id = str(identifier.get("value") or short_name)
        collected_start, collected_end = _catalog_temporal_dates(
            dataset.get("temporalCoverage")
        )
        year_text = (collected_end or collected_start or "")[:4]
        publication = str(dataset.get("dateCreated") or "")[:10] or None
        horizontal_crs = _catalog_property(dataset, "Horizontal Coordinates")
        horizontal_epsg = _catalog_property(dataset, "EPSG (Horizontal)")
        vertical_crs = _catalog_property(dataset, "Vertical Coordinates")
        name = str(dataset.get("name") or short_name)
        props = {
            "id": short_name,
            "opentopo_id": opentopo_id,
            "workunit": short_name,
            "name": name,
            "source_type": "opentopography_dem",
            "source_dem_products": products,
            "covers_aoi": True,
            "collection_start": collected_start,
            "collection_end": collected_end,
            "collection_year": int(year_text) if year_text.isdigit() else None,
            "quality_level": None,
            "dem_gsd_meters": resolution,
            "horizontal_crs": horizontal_crs or horizontal_epsg,
            "vertical_crs": vertical_crs,
            "geoid": None,
            "source_dem_publication_date": publication,
            "source_dem_category": "Not USGS validated",
            "year_estimated": False,
            "metadata_source": "OpenTopography Catalog/STAC",
            "metadata_link": dataset_page,
            "dataset_citation": collection.get("sci:citation")
            or dataset.get("citation"),
            "dataset_license": collection.get("license")
            or "See OpenTopography dataset page",
        }
        results[f"opentopography:{short_name.lower()}"] = {
            "id": short_name,
            "props": props,
        }

    _log(log, f"OpenTopography found {len(results)} public high-resolution DTM(s)")
    return results


def list_intersecting_datasets(
    bbox4326: Tuple[float, float, float, float],
    log: LogFunc = None,
    ranking_mode: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Return ranked USGS and OpenTopography datasets intersecting the requested AOI.

    Args:
        bbox4326: Bounding box (lon_min, lat_min, lon_max, lat_max) in EPSG:4326
        log: Optional logging function

    Returns:
        List of dicts with keys: id, props
    """
    wesm_records = ensure_wesm_metadata(log=log)
    lon_min, lat_min, lon_max, lat_max = bbox4326
    aoi_bbox = (float(lon_min), float(lat_min), float(lon_max), float(lat_max))
    candidates: Dict[str, Dict[str, Any]] = {}

    product_groups: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
    for dataset, field_name in (
        (TNM_OPR_DATASET, "source_dem_products"),
        (TNM_LPC_DATASET, "lpc_products"),
    ):
        try:
            products = _query_tnm_products(dataset, aoi_bbox, log=log)
        except Exception as exc:
            _log(log, f"USGS TNM {dataset} query failed ({exc})")
            products = []
        for product in products:
            key = _product_workunit_key(product, wesm_records)
            if not key:
                continue
            product_groups.setdefault(key, {}).setdefault(field_name, []).append(product)

    for key, grouped in product_groups.items():
        record = wesm_records.get(key) or {}
        workunit = record.get("workunit") or key
        props = _enrich_dataset_properties(workunit, {"name": workunit}, wesm_records)
        source_products = grouped.get("source_dem_products", [])
        lpc_products = grouped.get("lpc_products", [])
        props.update(grouped)
        props.update(
            {
                "id": workunit,
                "source_type": "source_dem" if source_products else "lpc",
                "covers_aoi": _products_cover_bbox(
                    source_products or lpc_products, aoi_bbox
                ),
            }
        )
        candidates[key] = {"id": workunit, "props": props}

    try:
        ept = _ept_candidates(aoi_bbox, wesm_records, log=log)
    except Exception as exc:
        _log(log, f"AWS EPT index unavailable ({exc})")
        ept = {}
    for key, ept_candidate in ept.items():
        if key in candidates:
            existing = candidates[key]["props"]
            ept_props = ept_candidate["props"]
            existing.update(
                {
                    "ept_dataset_id": ept_props.get("ept_dataset_id"),
                    "ept_url": ept_props.get("ept_url"),
                    "source_type": "ept",
                    "covers_aoi": bool(existing.get("covers_aoi"))
                    or bool(ept_props.get("covers_aoi")),
                }
            )
        else:
            candidates[key] = ept_candidate

    try:
        opentopography = _opentopography_candidates(aoi_bbox, log=log)
    except Exception as exc:
        _log(log, f"OpenTopography catalog unavailable ({exc})")
        opentopography = {}
    candidates.update(opentopography)

    if not candidates:
        return []

    if ranking_mode is None:
        ranking_mode = str(
            load_config().get("preferences", {}).get("dataset_ranking", "best_detail")
        )
    if ranking_mode not in {"best_detail", "newest"}:
        ranking_mode = "best_detail"

    result = list(candidates.values())
    result.sort(
        key=lambda item: _dataset_sort_key(
            item["id"], item["props"], ranking_mode=ranking_mode
        ),
        reverse=True,
    )
    _log(
        log,
        f"Combined catalog discovery found {len(result)} dataset(s); "
        f"ranking mode: {'Best Detail' if ranking_mode == 'best_detail' else 'Newest Survey'}",
    )
    return result


def select_dataset_for_bbox(
    bbox4326: Tuple[float, float, float, float],
    rank: int = 0,
    log: LogFunc = None
) -> Tuple[str, int, Dict[str, Any]]:
    """
    Select the Nth-ranked WESM work unit for the AOI.

    Args:
        bbox4326: Bounding box (lon_min, lat_min, lon_max, lat_max) in EPSG:4326
        rank: Zero-based index in the configured dataset ranking
        log: Optional logging function

    Returns:
        (delivery_url, epsg_code, dataset_properties). The EPSG code is zero
        for official tile downloads and is read from the downloaded data.

    Raises:
        RuntimeError if no datasets intersect or rank out of range
    """
    if rank < 0:
        rank = 0

    candidates = list_intersecting_datasets(bbox4326=bbox4326, log=log)

    if not candidates:
        raise RuntimeError(
            "No LiDAR datasets found for this location. Try a different area."
        )

    if rank >= len(candidates):
        raise RuntimeError(
            f"Requested dataset rank {rank} but only {len(candidates)} "
            "dataset(s) available."
        )

    chosen = candidates[rank]
    best_id = chosen["id"]
    best_props = dict(chosen["props"])
    source_type = best_props.get("source_type")
    if source_type == "ept":
        delivery_url = str(best_props["ept_url"])
        epsg = _get_ept_srs_epsg(delivery_url)
    else:
        products = best_props.get(
            "source_dem_products"
            if source_type in {"source_dem", "opentopography_dem"}
            else "lpc_products",
            [],
        )
        delivery_url = _product_download_url(products[0]) if products else None
        epsg = 0
    if not delivery_url:
        raise RuntimeError(f"Selected work unit has no usable download URL: {best_id}")

    best_props["id"] = best_id
    _log(log, f"Selected work unit: {best_id}")
    _log(log, f"Delivery: {source_type}")
    if best_props.get("collection_year"):
        suffix = " (estimated)" if best_props.get("year_estimated") else ""
        details = f"Collected: {best_props['collection_year']}{suffix}"
        if best_props.get("quality_level"):
            details += f", QL{best_props['quality_level']}"
        _log(log, details)

    return delivery_url, epsg, best_props


# ============================================================================
# LiDAR Download & Clipping
# ============================================================================

def find_pdal(config: dict = None) -> str:
    """
    Find PDAL executable.

    Checks:
    1. Bundled binary (in .app or bundle_bins/)
    2. Config file paths
    3. Environment variable PDAL_BIN
    4. Common installation paths
    5. System PATH

    Returns:
        Path to pdal executable

    Raises:
        FileNotFoundError if not found
    """
    import shutil
    import os
    import platform
    from utils.binary_paths import find_bundled_binary

    # Check for bundled binary first
    bundled = find_bundled_binary("pdal")
    if bundled:
        return bundled

    # Check config
    if config and config.get("paths", {}).get("pdal"):
        pdal_path = config["paths"]["pdal"]
        if Path(pdal_path).is_file():
            return pdal_path

    # Check environment variable
    if "PDAL_BIN" in os.environ:
        pdal_path = os.environ["PDAL_BIN"]
        if Path(pdal_path).is_file():
            return pdal_path

    # Check common paths
    system = platform.system()
    common_paths = []

    if system == "Darwin":  # macOS
        common_paths = [
            "/opt/homebrew/bin/pdal",  # Homebrew (Apple Silicon) - prioritized
            "/usr/local/bin/pdal",  # Homebrew (Intel)
            "/opt/local/bin/pdal",  # MacPorts
            "/Applications/MacPorts/QGIS3.app/Contents/MacOS/bin/pdal",
            "/Applications/QGIS.app/Contents/MacOS/bin/pdal",
        ]
    elif system == "Windows":
        # Check conda environment
        conda_prefix = os.environ.get("CONDA_PREFIX", "")
        if conda_prefix:
            common_paths.append(str(Path(conda_prefix) / "Library" / "bin" / "pdal.exe"))
        # Check common conda env locations
        home = Path.home()
        for env_name in ["lidar", "geo", "pdal"]:
            for base in [home / ".conda" / "envs", Path("C:/ProgramData/miniconda3/envs")]:
                candidate = base / env_name / "Library" / "bin" / "pdal.exe"
                common_paths.append(str(candidate))
        # Check QGIS
        for qgis_version in ["3.36", "3.34", "3.32", "3.30", "3.28"]:
            common_paths.append(f"C:\\Program Files\\QGIS {qgis_version}\\bin\\pdal.exe")
        common_paths.append(r"C:\OSGeo4W64\bin\pdal.exe")
        common_paths.append(r"C:\OSGeo4W\bin\pdal.exe")
    else:  # Linux
        common_paths = [
            "/usr/bin/pdal",
            "/usr/local/bin/pdal",
        ]

    for path in common_paths:
        if Path(path).is_file():
            return path

    # Check system PATH
    pdal_path = shutil.which("pdal")
    if pdal_path:
        return pdal_path

    raise FileNotFoundError(
        "PDAL not found. Please install PDAL (typically via QGIS installation)."
    )


def clip_aws_aoi_to_ground_laz(
    bbox4326: Tuple[float, float, float, float],
    center_lat: float,
    center_lon: float,
    square_miles: float,
    dataset_rank: int = 0,
    dataset: Optional[Dict[str, Any]] = None,
    log: LogFunc = None,
    cancel_check: Optional[Callable[[], bool]] = None
) -> Tuple[Path, Dict[str, Any], int]:
    """
    Download and clip AWS LiDAR dataset to AOI, keeping ground points only.

    Args:
        bbox4326: Bounding box (lon_min, lat_min, lon_max, lat_max) in EPSG:4326
        center_lat: AOI center latitude
        center_lon: AOI center longitude
        square_miles: AOI size in square miles
        dataset_rank: Zero-based index in the configured dataset ranking
        log: Optional logging function
        cancel_check: Optional function that returns True if cancelled

    Returns:
        (clipped_laz_path, dataset_properties, epsg_code)

    Raises:
        RuntimeError if download/clip fails or cancelled
    """
    output_dir = get_output_dir()
    laz_dir = output_dir / "laz"
    laz_dir.mkdir(parents=True, exist_ok=True)

    _log(log, "=== Downloading LiDAR data ===")
    _log(log, f"AOI bbox: {bbox4326}")
    _log(log, f"Dataset rank: {dataset_rank}")

    lon_min, lat_min, lon_max, lat_max = bbox4326

    # Select dataset unless the WESM-first discovery result was already supplied.
    if dataset is None:
        ept_url, epsg_code, ds_props = select_dataset_for_bbox(
            bbox4326=bbox4326,
            rank=dataset_rank,
            log=log,
        )
    else:
        ds_props = dict(dataset.get("props") or {})
        ept_url = ds_props.get("ept_url")
        epsg_code = _get_ept_srs_epsg(ept_url) if ept_url else 0
    if ds_props.get("source_type") != "ept" or not ept_url:
        raise RuntimeError("Selected work unit is not available through AWS EPT")

    ds_id = ds_props.get("id") or "unknown"

    # Build output filename (simplified - no AOI prefix or year)
    lat_str = f"{float(center_lat):.4f}"
    lon_str = f"{float(center_lon):.4f}"
    sqmi_str = f"{square_miles:.2f}".rstrip("0").rstrip(".")

    clipped_name = f"{lat_str}_{lon_str}_{sqmi_str}sqmi_ground.laz"
    clipped_laz = laz_dir / clipped_name

    _log(log, f"Output: {clipped_name}")

    # Convert bbox from EPSG:4326 to dataset CRS for proper clipping.
    #
    # Deliberately NOT pointing pyproj at a system Homebrew PROJ
    # install here (this used to do that). pyproj ships its own bundled,
    # version-matched proj.db and finds it automatically - `dem_generator.py`
    # uses plain `Transformer.from_crs(...)` the same way with no override
    # and that works fine. Forcing PROJ_LIB/PROJ_DATA/pyproj.datadir at an
    # arbitrary system install (only present on some dev machines, and not
    # guaranteed to match the version pyproj/rasterio were built against)
    # previously caused CRS-transform failures - notably KMZ export's
    # rasterio.warp.transform_bounds() - on end-user Macs. See AGENTS.md.
    from pyproj import Transformer

    target_epsg = f"EPSG:{epsg_code}"
    transformer = Transformer.from_crs("EPSG:4326", target_epsg, always_xy=True)

    # Transform all corners
    minx, miny = transformer.transform(lon_min, lat_min)
    maxx, maxy = transformer.transform(lon_max, lat_max)

    # Ensure correct order (pyproj should handle this, but be safe)
    if minx > maxx:
        minx, maxx = maxx, minx
    if miny > maxy:
        miny, maxy = maxy, miny

    # Fetch a small buffer around the requested AOI. The extra ground points
    # give boundary cells a complete neighborhood for triangulation. The DEM
    # raster is constrained back to the exact AOI bounds in dem_generator.py.
    config = load_config()
    try:
        tin_buffer_m = float(config.get("dem_fill", {}).get("tin_buffer_m", 20))
    except (TypeError, ValueError):
        tin_buffer_m = 20.0
    tin_buffer_m = max(0.0, min(100.0, tin_buffer_m))

    buffered_minx = minx - tin_buffer_m
    buffered_miny = miny - tin_buffer_m
    buffered_maxx = maxx + tin_buffer_m
    buffered_maxy = maxy + tin_buffer_m

    # PDAL bounds format: "([minx,maxx],[miny,maxy])"
    bounds_str = (
        f"([{buffered_minx},{buffered_maxx}],"
        f"[{buffered_miny},{buffered_maxy}])"
    )

    _log(log, f"TIN download buffer: {tin_buffer_m:g}m")
    _log(log, f"Buffered bounds in {target_epsg}: {bounds_str}")

    # Build PDAL pipeline using bounds (not polygon)
    pipeline_obj = {
        "pipeline": [
            {
                "type": "readers.ept",
                "filename": ept_url,
                "bounds": bounds_str,
            },
            {
                "type": "filters.range",
                "limits": "Classification[2:2]",  # Ground points only
            },
            {
                "type": "writers.las",
                "filename": str(clipped_laz),
                "compression": "laszip",
            },
        ]
    }

    pipeline_json = laz_dir / "pipeline.json"
    pipeline_json.write_text(json.dumps(pipeline_obj, indent=2), encoding="utf-8")

    # Find PDAL
    try:
        pdal_path = find_pdal(config)
    except FileNotFoundError as e:
        raise RuntimeError(str(e))

    # Run PDAL pipeline with cancellation support
    cmd = [pdal_path, "pipeline", str(pipeline_json)]
    _log(log, "Running PDAL pipeline...")

    # Use Popen so we can check for cancellation
    import time
    from utils.binary_paths import get_bundled_lib_env
    env = get_bundled_lib_env()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
                            creationflags=get_creation_flags(),
                            startupinfo=get_startupinfo())

    # Poll the process and check for cancellation
    while proc.poll() is None:
        if cancel_check and cancel_check():
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            raise RuntimeError("Cancelled by user")
        time.sleep(0.1)  # Check every 100ms

    # Get output after process completes
    stdout, stderr = proc.communicate()

    if proc.returncode != 0:
        raise RuntimeError(
            f"PDAL pipeline failed:\n{stderr or stdout}"
        )

    # Validate output
    if not clipped_laz.is_file():
        raise RuntimeError(f"Clipped LAZ was not created")

    size = clipped_laz.stat().st_size
    _log(log, f"Clipped LAZ size: {size:,} bytes")

    if size < MIN_LAZ_SIZE:
        raise RuntimeError(
            f"Clipped LAZ file is too small ({size} bytes). "
            "There may be insufficient LiDAR coverage for this area."
        )

    return clipped_laz, ds_props, epsg_code


def _download_product_file(
    product: Dict[str, Any],
    destination: Path,
    log: LogFunc = None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> Path:
    """Download one official TNM product with cancellation and URL validation."""
    url = _product_download_url(product)
    if not url:
        raise RuntimeError("USGS product record has no HTTPS download URL")
    host = (urlparse(url).hostname or "").lower()
    allowed_hosts = {
        "prd-tnm.s3.amazonaws.com",
        "rockyweb.usgs.gov",
        OPENTOPO_DATA_HOST,
    }
    if host not in allowed_hosts:
        raise RuntimeError(f"Unexpected USGS product host: {host or 'missing'}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    request = Request(url, headers={"User-Agent": "LiDAR-Hillshade-Explorer/6.3.1"})
    if HAS_CERTIFI:
        ssl_context = ssl.create_default_context(cafile=certifi.where())
    else:
        ssl_context = ssl.create_default_context()

    expected = int(product.get("sizeInBytes") or 0)
    _log(
        log,
        f"Downloading {destination.name}"
        + (f" ({expected / 1_000_000:.1f} MB)" if expected else ""),
    )
    downloaded = 0
    try:
        with urlopen(request, timeout=60, context=ssl_context) as response:
            with destination.open("wb") as output:
                while True:
                    if cancel_check and cancel_check():
                        raise RuntimeError("Cancelled by user")
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
                    downloaded += len(chunk)
    except Exception:
        destination.unlink(missing_ok=True)
        raise

    if expected and downloaded < max(1024, int(expected * 0.95)):
        destination.unlink(missing_ok=True)
        raise RuntimeError(
            f"Incomplete USGS download for {destination.name}: "
            f"received {downloaded:,} of {expected:,} bytes"
        )
    return destination


def _output_basename(center_lat: float, center_lon: float, square_miles: float) -> str:
    sqmi = f"{square_miles:.2f}".rstrip("0").rstrip(".")
    return f"{float(center_lat):.4f}_{float(center_lon):.4f}_{sqmi}sqmi"


def download_source_dem_for_aoi(
    dataset: Dict[str, Any],
    bbox4326: Tuple[float, float, float, float],
    center_lat: float,
    center_lon: float,
    square_miles: float,
    log: LogFunc = None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> Tuple[Path, Dict[str, Any], int, float]:
    """Crop/mosaic an official USGS or public OpenTopography source DTM."""
    import numpy as np
    import rasterio
    from rasterio.merge import merge
    from rasterio.warp import transform_bounds

    props = dict(dataset.get("props") or {})
    products = props.get("source_dem_products") or []
    if not products:
        raise RuntimeError("Selected dataset has no source DEM tiles")

    dem_dir = get_output_dir() / "dem"
    dem_dir.mkdir(parents=True, exist_ok=True)
    dem_path = dem_dir / f"{_output_basename(center_lat, center_lon, square_miles)}.tif"

    is_opentopography = props.get("source_type") == "opentopography_dem"
    source_label = (
        "OpenTopography DTM"
        if is_opentopography
        else "official USGS source DEM"
    )
    _log(log, f"=== Preparing {source_label} ===")
    _log(log, f"Dataset: {props.get('workunit') or dataset.get('id')}")
    with tempfile.TemporaryDirectory(prefix="source_dem_", dir=dem_dir) as temp_dir:
        if is_opentopography:
            _log(log, "Reading public cloud-optimized DTM tiles...")
            sources = [
                rasterio.open(str(_product_download_url(product)))
                for product in products
            ]
        else:
            tile_paths = []
            for index, product in enumerate(products):
                url = _product_download_url(product) or ""
                filename = (
                    Path(unquote(urlparse(url).path)).name or f"tile_{index}.tif"
                )
                tile_paths.append(
                    _download_product_file(
                        product,
                        Path(temp_dir) / filename,
                        log=log,
                        cancel_check=cancel_check,
                    )
                )
            sources = [rasterio.open(path) for path in tile_paths]
        try:
            source_crs = sources[0].crs
            if source_crs is None:
                raise RuntimeError("Source DEM tile has no coordinate system")
            if any(source.crs != source_crs for source in sources[1:]):
                raise RuntimeError("Source DEM tiles use inconsistent coordinate systems")
            projected_bounds = transform_bounds(
                "EPSG:4326", source_crs, *bbox4326, densify_pts=21
            )
            nodata = sources[0].nodata
            if nodata is None:
                nodata = -9999.0
            mosaic, transform = merge(
                sources,
                bounds=projected_bounds,
                nodata=nodata,
                dtype="float32",
            )
            if cancel_check and cancel_check():
                raise RuntimeError("Cancelled by user")
            valid = np.isfinite(mosaic[0]) & (mosaic[0] != nodata)
            if not np.any(valid):
                raise RuntimeError("Source DEM contains no data in the requested area")
            profile = sources[0].profile.copy()
            profile.update(
                driver="GTiff",
                width=mosaic.shape[2],
                height=mosaic.shape[1],
                count=1,
                dtype="float32",
                crs=source_crs,
                transform=transform,
                nodata=nodata,
                compress="LZW",
                tiled=True,
            )
            with rasterio.open(dem_path, "w", **profile) as output:
                output.write(mosaic[0].astype("float32"), 1)
            resolution = (abs(float(transform.a)) + abs(float(transform.e))) / 2.0
            epsg = source_crs.to_epsg() or 0
        finally:
            for source in sources:
                source.close()

    _log(log, f"Source DEM ready: {dem_path.name} ({resolution:g} m)")
    return dem_path, props, int(epsg), resolution


def download_lpc_for_aoi(
    dataset: Dict[str, Any],
    bbox4326: Tuple[float, float, float, float],
    center_lat: float,
    center_lon: float,
    square_miles: float,
    log: LogFunc = None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> Tuple[Path, Dict[str, Any], int]:
    """Download official LAZ tiles, retain ground points, and clip to the AOI."""
    from pyproj import Transformer
    from utils.binary_paths import get_bundled_lib_env

    props = dict(dataset.get("props") or {})
    products = props.get("lpc_products") or []
    if not products:
        raise RuntimeError("Selected WESM work unit has no official LPC tiles")

    laz_dir = get_output_dir() / "laz"
    laz_dir.mkdir(parents=True, exist_ok=True)
    output_path = laz_dir / f"{_output_basename(center_lat, center_lon, square_miles)}_ground.laz"
    zone = max(1, min(60, int((center_lon + 180) // 6) + 1))
    epsg = (32600 if center_lat >= 0 else 32700) + zone
    transformer = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
    minx, miny = transformer.transform(bbox4326[0], bbox4326[1])
    maxx, maxy = transformer.transform(bbox4326[2], bbox4326[3])
    minx, maxx = sorted((minx, maxx))
    miny, maxy = sorted((miny, maxy))

    config = load_config()
    try:
        buffer_m = float(config.get("dem_fill", {}).get("tin_buffer_m", 20))
    except (TypeError, ValueError):
        buffer_m = 20.0
    buffer_m = max(0.0, min(100.0, buffer_m))
    bounds = (
        f"([{minx - buffer_m},{maxx + buffer_m}],"
        f"[{miny - buffer_m},{maxy + buffer_m}])"
    )

    _log(log, "=== Downloading official USGS LPC tiles ===")
    with tempfile.TemporaryDirectory(prefix="lpc_", dir=laz_dir) as temp_dir:
        readers = []
        tags = []
        for index, product in enumerate(products):
            url = _product_download_url(product) or ""
            filename = Path(unquote(urlparse(url).path)).name or f"tile_{index}.laz"
            tile = _download_product_file(
                product,
                Path(temp_dir) / filename,
                log=log,
                cancel_check=cancel_check,
            )
            tag = f"tile_{index}"
            tags.append(tag)
            readers.append({"type": "readers.las", "filename": str(tile), "tag": tag})

        pipeline: List[Dict[str, Any]] = list(readers)
        previous_inputs: Any = tags[0]
        if len(tags) > 1:
            pipeline.append({"type": "filters.merge", "inputs": tags, "tag": "merged"})
            previous_inputs = "merged"
        pipeline.extend(
            [
                {
                    "type": "filters.range",
                    "inputs": previous_inputs,
                    "limits": "Classification[2:2]",
                    "tag": "ground",
                },
                {
                    "type": "filters.reprojection",
                    "inputs": "ground",
                    "out_srs": f"EPSG:{epsg}",
                    "tag": "projected",
                },
                {
                    "type": "filters.crop",
                    "inputs": "projected",
                    "bounds": bounds,
                    "tag": "cropped",
                },
                {
                    "type": "writers.las",
                    "inputs": "cropped",
                    "filename": str(output_path),
                    "compression": "laszip",
                },
            ]
        )
        pipeline_path = laz_dir / "official_lpc_pipeline.json"
        pipeline_path.write_text(json.dumps({"pipeline": pipeline}, indent=2), encoding="utf-8")
        pdal_path = find_pdal(config)
        proc = subprocess.Popen(
            [pdal_path, "pipeline", str(pipeline_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=get_bundled_lib_env(),
            creationflags=get_creation_flags(),
            startupinfo=get_startupinfo(),
        )
        while proc.poll() is None:
            if cancel_check and cancel_check():
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                raise RuntimeError("Cancelled by user")
            time.sleep(0.1)
        stdout, stderr = proc.communicate()
        if proc.returncode != 0:
            output_path.unlink(missing_ok=True)
            raise RuntimeError(f"Official LPC processing failed:\n{stderr or stdout}")

    if not output_path.is_file() or output_path.stat().st_size < MIN_LAZ_SIZE:
        output_path.unlink(missing_ok=True)
        raise RuntimeError("Official LPC tiles contained insufficient ground data")
    return output_path, props, epsg


def validate_laz_point_density(
    laz_path: Path,
    square_miles: float,
    log: LogFunc = None
) -> bool:
    """
    Validate that LAZ file has sufficient point density.

    Args:
        laz_path: Path to LAZ file
        square_miles: AOI size in square miles
        log: Optional logging function

    Returns:
        True if density is sufficient, False otherwise
    """
    try:
        config = load_config()
        pdal_path = find_pdal(config)

        # Run pdal info to get point count
        from utils.binary_paths import get_bundled_lib_env
        env = get_bundled_lib_env()
        cmd = [pdal_path, "info", "--metadata", str(laz_path)]
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env,
                          creationflags=get_creation_flags(),
                          startupinfo=get_startupinfo())

        if proc.returncode != 0:
            _log(log, "Warning: Could not validate point density")
            return True  # Assume OK if we can't check

        metadata = json.loads(proc.stdout)
        point_count = metadata.get("metadata", {}).get("count", 0)

        if point_count == 0:
            _log(log, "Warning: No points found in LAZ file")
            return False

        density = point_count / square_miles
        _log(log, f"Point density: {density:,.0f} points/sq mi ({point_count:,} total points)")

        if density < MIN_POINTS_PER_SQ_MI:
            _log(log, f"Warning: Point density below minimum ({MIN_POINTS_PER_SQ_MI:,} pts/sq mi)")
            return False

        return True

    except Exception as e:
        _log(log, f"Warning: Error validating point density: {e}")
        return True  # Assume OK on error
