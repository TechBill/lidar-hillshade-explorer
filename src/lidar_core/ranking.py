"""Dataset metadata enrichment and ranking rules."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional


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
