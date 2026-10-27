#!/usr/bin/env python3
"""Compatibility facade for catalog discovery, ranking, and delivery.

Implementations live in ``catalogs/``, ``delivery/``, and ``ranking.py``.
Existing callers may continue importing this module's historical API.
"""

from lidar_core.catalogs.ept import (
    AWS_INDEX_URL,
    CACHE_EXPIRY_DAYS,
    _bbox_from_geometry,
    _ept_candidates,
    _get_ept_srs_epsg,
    ensure_aws_index,
)
from lidar_core.catalogs.opentopography import (
    OPENTOPO_CATALOG_URL,
    OPENTOPO_DATA_HOST,
    OPENTOPO_STAC_BASE_URL,
    _catalog_area_sq_km,
    _catalog_geometry,
    _catalog_property,
    _catalog_temporal_dates,
    _fetch_json_url,
    _opentopography_candidates,
    _opentopography_item_for_collection,
    _remote_raster_resolution,
)
from lidar_core.catalogs.usgs import (
    MIN_WESM_RECORDS,
    REQUIRED_WESM_FIELDS,
    TNM_LPC_DATASET,
    TNM_OPR_DATASET,
    TNM_PRODUCTS_URL,
    WESM_CACHE_EXPIRY_DAYS,
    WESM_URL,
    _bbox_intersects,
    _extract_dataset_id,
    _geometry_covers_bbox,
    _geometry_intersects_bbox,
    _log,
    _parse_wesm_csv,
    _point_in_bbox,
    _point_in_ring,
    _polygon_intersects_bbox,
    _product_bbox,
    _product_download_url,
    _product_workunit_key,
    _products_cover_bbox,
    _query_tnm_products,
    _ring_intersects_bbox,
    _ring_points,
    _segments_intersect,
    _validate_wesm_records,
    ensure_wesm_metadata,
    list_intersecting_datasets,
    select_dataset_for_bbox,
)
from lidar_core.delivery.point_cloud import (
    MIN_LAZ_SIZE,
    MIN_POINTS_PER_SQ_MI,
    _download_product_file,
    _output_basename,
    clip_aws_aoi_to_ground_laz,
    download_lpc_for_aoi,
    find_pdal,
    validate_laz_point_density,
)
from lidar_core.delivery.raster_dem import download_source_dem_for_aoi
from lidar_core.ranking import (
    _dataset_sort_key,
    _date_value,
    _enrich_dataset_properties,
    _extract_year,
    _metadata_for_dataset,
    _normalize_dataset_name,
    _quality_level,
    _validation_score,
    _wesm_lookup_keys,
)

__all__ = [
    "clip_aws_aoi_to_ground_laz",
    "download_lpc_for_aoi",
    "download_source_dem_for_aoi",
    "ensure_aws_index",
    "ensure_wesm_metadata",
    "find_pdal",
    "list_intersecting_datasets",
    "select_dataset_for_bbox",
    "validate_laz_point_density",
]
