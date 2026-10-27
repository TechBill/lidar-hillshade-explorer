"""Public OpenTopography DTM catalog and STAC discovery."""

from __future__ import annotations

import json
import math
import re
import ssl
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

try:
    import certifi
    HAS_CERTIFI = True
except ImportError:
    HAS_CERTIFI = False

from lidar_core.catalogs.usgs import (
    LogFunc,
    _bbox_intersects,
    _geometry_covers_bbox,
    _log,
    _products_cover_bbox,
)
from lidar_core.catalogs.cache import (
    load_catalog_cache,
    load_stale_catalog_cache,
    save_catalog_cache,
)

OPENTOPO_CATALOG_URL = "https://portal.opentopography.org/API/otCatalog"
OPENTOPO_STAC_BASE_URL = "https://portal.opentopography.org/stac"
OPENTOPO_DATA_HOST = "opentopography.s3.sdsc.edu"


def _fetch_json_url(url: str, timeout: int = 25) -> Dict[str, Any]:
    """Fetch a public JSON document with the app's normal TLS handling."""
    request = Request(
        url,
        headers={"User-Agent": "LiDAR-Hillshade-Explorer/6.5"},
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

    with rasterio.Env(
        GDAL_HTTP_CONNECTTIMEOUT="10",
        GDAL_HTTP_TIMEOUT="25",
        GDAL_HTTP_MAX_RETRY="2",
        GDAL_HTTP_RETRY_DELAY="1",
    ):
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
    use_cache: bool = True,
) -> Dict[str, Dict[str, Any]]:
    """Discover public, localized lidar DTMs from OpenTopography's catalog."""
    if use_cache:
        cached = load_catalog_cache("opentopography", bbox4326)
        if isinstance(cached, dict):
            _log(log, "OpenTopography: using cached catalog results")
            return cached

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
    try:
        payload = _fetch_json_url(f"{OPENTOPO_CATALOG_URL}?{query}")
    except Exception:
        stale = load_stale_catalog_cache("opentopography", bbox4326) if use_cache else None
        if isinstance(stale, dict):
            _log(log, "OpenTopography unavailable; using recent cached results")
            return stale
        raise
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
    if use_cache:
        save_catalog_cache("opentopography", bbox4326, results)
    return results
