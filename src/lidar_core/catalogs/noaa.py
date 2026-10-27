"""NOAA Coastal LiDAR discovery using USIEI footprints and public EPT."""

from __future__ import annotations

from typing import Any, Dict, Iterable, Tuple

from lidar_core.catalogs.usgs import LogFunc, _log
from lidar_core.catalogs.usiei import (
    _ept_has_ground_class,
    _ept_links,
    _is_noaa_ept_url,
    _is_usable_record,
    _project_identity,
    _record_metadata,
)


NOAA_CATALOG_URL = "https://noaa-nos-coastal-lidar-pds.s3.amazonaws.com/laz/index.html"
NOAA_LICENSE = (
    "NOAA public data; attribution requested; no NOAA endorsement implied"
)


def _noaa_candidates(
    records: Iterable[Dict[str, Any]],
    bbox4326: Tuple[float, float, float, float],
    log: LogFunc = None,
) -> Dict[str, Dict[str, Any]]:
    """Build conventional topographic NOAA EPT candidates covering the AOI."""
    candidates: Dict[str, Dict[str, Any]] = {}
    for feature in records:
        if not _is_usable_record(feature, bbox4326):
            continue
        record = feature.get("properties") or {}
        for ept_url in _ept_links(record):
            if not _is_noaa_ept_url(ept_url):
                continue
            if not _ept_has_ground_class(ept_url, record.get("pointclasses")):
                continue
            metadata = _record_metadata(feature)
            mission_id = record.get("missionid") or record.get("ID")
            title = str(record.get("Title") or f"NOAA Coastal LiDAR {mission_id}").strip()
            metadata.update(
                {
                    "id": title,
                    "workunit": title,
                    "ept_dataset_id": f"NOAA_{mission_id}",
                    "ept_url": ept_url,
                    "source_type": "noaa_ept",
                    "covers_aoi": True,
                    "metadata_source": "NOAA USIEI / Coastal LiDAR",
                    "dataset_license": NOAA_LICENSE,
                    "catalog_provider": "NOAA Coastal LiDAR",
                    "noaa_catalog_url": NOAA_CATALOG_URL,
                    "usiei_project_identity": _project_identity(
                        record.get("AlternateTitle") or title
                    ),
                }
            )
            candidates[f"noaa:{mission_id}"] = {"id": title, "props": metadata}
    _log(log, f"NOAA Coastal LiDAR added {len(candidates)} topographic EPT dataset(s)")
    return candidates
