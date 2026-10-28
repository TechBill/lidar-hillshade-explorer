"""Authoritative USGS WESM/TNM catalog discovery."""

from __future__ import annotations

import csv
import io
import json
import ssl
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote, urlencode, urlparse
from urllib.request import Request, urlopen

try:
    import certifi
    HAS_CERTIFI = True
except ImportError:
    HAS_CERTIFI = False

from utils.config import get_cache_dir, load_config
from lidar_core.ranking import (
    _dataset_sort_key,
    _enrich_dataset_properties,
    _metadata_for_dataset,
    _normalize_dataset_name,
)

WESM_URL = (
    "https://prd-tnm.s3.amazonaws.com/"
    "StagedProducts/Elevation/metadata/WESM.csv"
)
TNM_PRODUCTS_URL = "https://tnmaccess.nationalmap.gov/api/v1/products"
TNM_OPR_DATASET = "Original Product Resolution (OPR) Digital Elevation Model (DEM)"
TNM_LPC_DATASET = "Lidar Point Cloud (LPC)"
WESM_CACHE_EXPIRY_DAYS = 7
MIN_WESM_RECORDS = 1000
REQUIRED_WESM_FIELDS = {
    "workunit", "collect_start", "collect_end", "ql", "dem_gsd_meters",
    "horiz_crs", "vert_crs", "geoid", "lpc_pub_date", "lpc_category",
    "sourcedem_pub_date", "sourcedem_category", "lpc_link",
    "sourcedem_link", "metadata_link",
}

LogFunc = Optional[callable]


def _log(log: LogFunc, msg: str) -> None:
    """Helper to log message if logger provided."""
    if log is not None:
        log(msg)


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
        with urlopen(WESM_URL, timeout=25, context=ssl_context) as response:
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
            headers={"User-Agent": "LiDAR-Hillshade-Explorer/6.5.4"},
        )
        if HAS_CERTIFI:
            ssl_context = ssl.create_default_context(cafile=certifi.where())
        else:
            ssl_context = ssl.create_default_context()
        with urlopen(request, timeout=25, context=ssl_context) as response:
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


def _list_intersecting_datasets_sequential(
    bbox4326: Tuple[float, float, float, float],
    log: LogFunc = None,
    ranking_mode: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Return ranked USGS, OpenTopography, NOAA, and USIEI datasets for the AOI.

    Args:
        bbox4326: Bounding box (lon_min, lat_min, lon_max, lat_max) in EPSG:4326
        log: Optional logging function

    Returns:
        List of dicts with keys: id, props
    """
    from lidar_core.catalogs.ept import _ept_candidates
    from lidar_core.catalogs.noaa import _noaa_candidates
    from lidar_core.catalogs.opentopography import _opentopography_candidates
    from lidar_core.catalogs.usiei import (
        _merge_usiei_metadata,
        _project_identity,
        _query_usiei_topographic,
        _usiei_candidates,
    )

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
        usiei_records = _query_usiei_topographic(aoi_bbox, log=log)
    except Exception as exc:
        _log(log, f"USIEI catalog unavailable ({exc})")
        usiei_records = []

    try:
        usiei = _usiei_candidates(
            usiei_records, aoi_bbox, wesm_records, log=log
        )
    except Exception as exc:
        _log(log, f"USIEI delivery lookup unavailable ({exc})")
        usiei = {}
    for key, usiei_candidate in usiei.items():
        if key not in candidates:
            candidates[key] = usiei_candidate

    # Project-level USIEI records enrich matching USGS work units. WESM
    # remains authoritative and these fields are attached under usiei_* keys.
    _merge_usiei_metadata(candidates, usiei_records)

    try:
        noaa = _noaa_candidates(usiei_records, aoi_bbox, log=log)
    except Exception as exc:
        _log(log, f"NOAA Coastal LiDAR lookup unavailable ({exc})")
        noaa = {}
    existing_project_identities = {
        _project_identity(
            item.get("props", {}).get("workunit")
            or item.get("props", {}).get("id")
            or item.get("id")
        )
        for item in candidates.values()
    }
    for key, noaa_candidate in noaa.items():
        noaa_props = noaa_candidate.get("props", {})
        identity = noaa_props.get("usiei_project_identity")
        if identity and identity in existing_project_identities:
            # The survey is already represented by its preferred USGS route.
            # Retain NOAA as an alternate delivery without showing a duplicate.
            for existing in candidates.values():
                existing_props = existing.get("props", {})
                if _project_identity(
                    existing_props.get("workunit")
                    or existing_props.get("id")
                    or existing.get("id")
                ) == identity:
                    existing_props["noaa_ept_url"] = noaa_props.get("ept_url")
                    existing_props["noaa_mission_id"] = noaa_props.get("noaa_mission_id")
                    break
            continue
        candidates[key] = noaa_candidate
        if identity:
            existing_project_identities.add(identity)

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
        f"Combined USGS/OpenTopography/NOAA/USIEI discovery found "
        f"{len(result)} dataset(s); "
        f"ranking mode: {'Best Detail' if ranking_mode == 'best_detail' else 'Newest Survey'}",
    )
    return result


def list_intersecting_datasets(
    bbox4326: Tuple[float, float, float, float],
    log: LogFunc = None,
    ranking_mode: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Compatibility wrapper for the concurrent discovery coordinator."""
    from lidar_core.discovery import list_intersecting_datasets as discover_list

    return discover_list(bbox4326, log=log, ranking_mode=ranking_mode)


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
    from lidar_core.catalogs.ept import _get_ept_srs_epsg

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
    if source_type in {"ept", "noaa_ept", "usiei_ept"}:
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
