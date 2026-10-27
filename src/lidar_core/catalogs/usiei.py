"""USIEI topographic-lidar discovery and supported EPT fallback delivery."""

from __future__ import annotations

import json
import re
import ssl
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import unquote, urlencode, urlparse
from urllib.request import Request, urlopen

try:
    import certifi
    HAS_CERTIFI = True
except ImportError:
    HAS_CERTIFI = False

from lidar_core.catalogs.usgs import LogFunc, _geometry_covers_bbox, _log
from lidar_core.catalogs.cache import (
    load_catalog_cache,
    load_stale_catalog_cache,
    save_catalog_cache,
)
from lidar_core.ranking import (
    _enrich_dataset_properties,
    _metadata_for_dataset,
    _normalize_dataset_name,
)


USIEI_TOPO_QUERY_URL = (
    "https://maps.coast.noaa.gov/arcgis/rest/services/"
    "USInteragencyElevationInventory/USIEIv2/MapServer/2/query"
)
USIEI_CATALOG_URL = "https://coast.noaa.gov/inventory/"
USIEI_OUT_FIELDS = ",".join(
    (
        "ID", "Title", "DataType", "Status", "Links", "pointspacing",
        "collectiondate", "collectionyear", "qualitylevel", "meets3dep",
        "meets3dep_lpc", "productsavailable", "pointclasses",
        "verticaldatum", "horizontaldatum", "restrictions", "leafOnOff",
        "AlternateTitle", "RecordOwner", "ContractSpec", "PTSID",
        "missionid",
    )
)


def _ssl_context() -> ssl.SSLContext:
    return ssl.create_default_context(cafile=certifi.where()) if HAS_CERTIFI else ssl.create_default_context()


def _fetch_json_url(url: str, timeout: int = 30) -> Dict[str, Any]:
    request = Request(
        url,
        headers={"User-Agent": "LiDAR-Hillshade-Explorer/6.5"},
    )
    with urlopen(request, timeout=timeout, context=_ssl_context()) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise ValueError("Catalog response was not a JSON object")
    return payload


def _query_usiei_topographic(
    bbox4326: Tuple[float, float, float, float],
    log: LogFunc = None,
    use_cache: bool = True,
) -> List[Dict[str, Any]]:
    """Query USIEI's topographic-lidar layer for an AOI."""
    if use_cache:
        cached = load_catalog_cache("usiei", bbox4326)
        if isinstance(cached, list):
            _log(log, f"USIEI: using {len(cached)} cached topographic record(s)")
            return cached

    bbox_text = ",".join(f"{float(value):.10f}" for value in bbox4326)
    query = urlencode(
        {
            "f": "geojson",
            "where": "1=1",
            "geometry": bbox_text,
            "geometryType": "esriGeometryEnvelope",
            "inSR": 4326,
            "outSR": 4326,
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": USIEI_OUT_FIELDS,
            "returnGeometry": "true",
            "geometryPrecision": 7,
        }
    )
    try:
        payload = _fetch_json_url(f"{USIEI_TOPO_QUERY_URL}?{query}", timeout=25)
    except Exception:
        stale = load_stale_catalog_cache("usiei", bbox4326) if use_cache else None
        if isinstance(stale, list):
            _log(log, f"USIEI unavailable; using {len(stale)} recent cached record(s)")
            return stale
        raise
    error = payload.get("error")
    if isinstance(error, dict):
        details = error.get("details") or []
        raise RuntimeError(error.get("message") or "; ".join(map(str, details)))
    features = [
        feature
        for feature in payload.get("features", [])
        if isinstance(feature, dict)
    ]
    _log(log, f"USIEI found {len(features)} intersecting topographic record(s)")
    if use_cache:
        save_catalog_cache("usiei", bbox4326, features)
    return features


def _usiei_links(props: Dict[str, Any]) -> List[Dict[str, str]]:
    """Decode USIEI's JSON-in-a-string Links field."""
    value = props.get("Links")
    if not value:
        return []
    try:
        payload = json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError):
        return []
    links = payload.get("links", []) if isinstance(payload, dict) else []
    return [link for link in links if isinstance(link, dict)]


def _ept_links(props: Dict[str, Any]) -> List[str]:
    urls: List[str] = []
    for link in _usiei_links(props):
        url = str(link.get("link") or "").strip()
        link_type = str(link.get("linktype") or "").strip().lower()
        if url.startswith("https://") and (
            link_type == "ept link" or url.lower().endswith("/ept.json")
        ):
            urls.append(url)
    return urls


def _first_link(props: Dict[str, Any], *link_types: str) -> Optional[str]:
    wanted = {value.lower() for value in link_types}
    for link in _usiei_links(props):
        if str(link.get("linktype") or "").lower() in wanted:
            url = str(link.get("link") or "").strip()
            if url.startswith("https://"):
                return url
    return None


def _is_noaa_ept_url(url: str) -> bool:
    parsed = urlparse(url)
    return (
        parsed.scheme == "https"
        and parsed.hostname in {
            "noaa-nos-coastal-lidar-pds.s3.amazonaws.com",
            "noaa-nos-coastal-lidar-pds.s3.us-east-1.amazonaws.com",
        }
        and parsed.path.startswith("/entwine/")
        and parsed.path.endswith("/ept.json")
    )


def _is_usgs_ept_url(url: str) -> bool:
    parsed = urlparse(url)
    return (
        parsed.scheme == "https"
        and (
            (
                parsed.hostname == "s3-us-west-2.amazonaws.com"
                and parsed.path.startswith("/usgs-lidar-public/")
            )
            or parsed.hostname == "usgs-lidar-public.s3.amazonaws.com"
        )
        and parsed.path.endswith("/ept.json")
    )


def _parse_point_spacing(value: Any) -> Optional[float]:
    match = re.search(r"(?<!\d)(\d+(?:\.\d+)?)\s*m\b", str(value or ""), re.IGNORECASE)
    if not match:
        return None
    spacing = float(match.group(1))
    return spacing if spacing > 0 else None


def _declared_point_classes(value: Any) -> Optional[set[int]]:
    if value is None or not str(value).strip():
        return None
    return {int(token) for token in re.findall(r"\d+", str(value))}


def _ept_has_ground_class(url: str, point_classes: Any = None) -> bool:
    """Require class 2 from declared metadata or the live EPT schema."""
    declared = _declared_point_classes(point_classes)
    if declared is not None:
        return 2 in declared
    try:
        payload = _fetch_json_url(url, timeout=15)
        for dimension in payload.get("schema", []):
            if str(dimension.get("name") or "").lower() != "classification":
                continue
            counts = dimension.get("counts") or []
            if counts:
                return any(int(item.get("value", -1)) == 2 for item in counts)
            minimum = int(dimension.get("minimum", -1))
            maximum = int(dimension.get("maximum", -1))
            return minimum <= 2 <= maximum
    except Exception:
        return False
    return False


def _ept_dataset_id(url: str) -> Optional[str]:
    parts = [unquote(part) for part in urlparse(url).path.split("/") if part]
    if len(parts) < 2 or parts[-1].lower() != "ept.json":
        return None
    return parts[-2]


def _project_identity(value: Any) -> str:
    """Normalize project and work-unit aliases for conservative deduplication."""
    text = re.sub(r"^\s*USGS\s*:\s*", "", str(value or ""), flags=re.IGNORECASE)
    text = re.sub(r"_(\d+)_([A-Z]\d{2})$", r"_\2", text, flags=re.IGNORECASE)
    return _normalize_dataset_name(text)


def _record_metadata(feature: Dict[str, Any]) -> Dict[str, Any]:
    props = feature.get("properties") or {}
    year = props.get("collectionyear")
    try:
        year = int(year) if year is not None else None
    except (TypeError, ValueError):
        year = None
    quality = props.get("qualitylevel")
    try:
        quality = int(quality) if quality is not None else None
    except (TypeError, ValueError):
        quality = None
    spacing = _parse_point_spacing(props.get("pointspacing"))
    meets_lpc = str(props.get("meets3dep_lpc") or "").strip().lower()
    return {
        "collection_year": year,
        "quality_level": quality,
        "point_spacing_meters": spacing,
        "horizontal_crs": props.get("horizontaldatum") or None,
        "vertical_crs": props.get("verticaldatum") or None,
        "lpc_category": "Meets" if meets_lpc == "yes" else "USIEI public lidar",
        "metadata_source": "NOAA USIEI",
        "metadata_link": _first_link(props, "Metadata", "Resources") or USIEI_CATALOG_URL,
        "data_access_link": _first_link(props, "Data Access"),
        "dataset_license": "Public; see source record restrictions",
        "dataset_citation": props.get("Title") or None,
        "catalog_provider": "USIEI",
        "record_owner": props.get("RecordOwner") or None,
        "usiei_id": props.get("ID"),
        "noaa_mission_id": props.get("missionid"),
        "point_classes": props.get("pointclasses") or None,
        "collection_description": props.get("collectiondate") or None,
        "contract_spec": props.get("ContractSpec") or None,
    }


def _is_usable_record(
    feature: Dict[str, Any],
    bbox4326: Tuple[float, float, float, float],
) -> bool:
    props = feature.get("properties") or {}
    if str(props.get("DataType") or "").lower() != "lidar-topo":
        return False
    if str(props.get("Status") or "").lower() != "complete":
        return False
    if str(props.get("restrictions") or "").lower() not in {"", "public"}:
        return False
    return _geometry_covers_bbox(feature.get("geometry") or {}, bbox4326)


def _usiei_candidates(
    records: Iterable[Dict[str, Any]],
    bbox4326: Tuple[float, float, float, float],
    wesm_records: Dict[str, Dict[str, str]],
    log: LogFunc = None,
) -> Dict[str, Dict[str, Any]]:
    """Build fallback candidates from USIEI records with official USGS EPT."""
    candidates: Dict[str, Dict[str, Any]] = {}
    for feature in records:
        if not _is_usable_record(feature, bbox4326):
            continue
        props = feature.get("properties") or {}
        for ept_url in _ept_links(props):
            if not _is_usgs_ept_url(ept_url):
                continue
            if not _ept_has_ground_class(ept_url, props.get("pointclasses")):
                continue
            dataset_id = _ept_dataset_id(ept_url)
            if not dataset_id:
                continue
            record = _metadata_for_dataset(dataset_id, wesm_records)
            workunit = (record or {}).get("workunit") or dataset_id
            enriched = _enrich_dataset_properties(dataset_id, {"name": dataset_id}, wesm_records)
            for name, value in _record_metadata(feature).items():
                if enriched.get(name) in {None, ""}:
                    enriched[name] = value
            enriched.update(
                {
                    "id": workunit,
                    "workunit": workunit,
                    "ept_dataset_id": dataset_id,
                    "ept_url": ept_url,
                    "source_type": "usiei_ept",
                    "covers_aoi": True,
                    "usiei_project_identity": _project_identity(
                        props.get("AlternateTitle") or props.get("Title")
                    ),
                }
            )
            candidates[_normalize_dataset_name(workunit)] = {
                "id": workunit,
                "props": enriched,
            }
    _log(log, f"USIEI added {len(candidates)} supported USGS EPT fallback(s)")
    return candidates


def _merge_usiei_metadata(
    candidates: Dict[str, Dict[str, Any]],
    records: Iterable[Dict[str, Any]],
) -> None:
    """Attach project-level USIEI metadata without replacing delivery metadata."""
    identities: Dict[str, List[Dict[str, Any]]] = {}
    for feature in records:
        props = feature.get("properties") or {}
        identity = _project_identity(props.get("AlternateTitle") or props.get("Title"))
        if identity:
            identities.setdefault(identity, []).append(feature)

    for candidate in candidates.values():
        props = candidate.get("props") or {}
        candidate_identity = _project_identity(
            props.get("workunit") or props.get("id") or candidate.get("id")
        )
        matches = identities.get(candidate_identity, [])
        if not matches:
            continue
        metadata = _record_metadata(matches[0])
        props["usiei_id"] = metadata.get("usiei_id")
        props["usiei_metadata_link"] = metadata.get("metadata_link")
        props["usiei_record_owner"] = metadata.get("record_owner")
        props["usiei_point_spacing_meters"] = metadata.get("point_spacing_meters")
        props["usiei_catalog_url"] = USIEI_CATALOG_URL

