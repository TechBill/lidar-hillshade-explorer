"""Concurrent, reusable catalog discovery for all supported US providers."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from lidar_core.catalogs import ept, noaa, opentopography, usgs, usiei
from lidar_core.ranking import _dataset_sort_key, _enrich_dataset_properties
from utils.config import load_config


LogFunc = Optional[Callable[[str], None]]
DISCOVERY_TIMEOUT_SECONDS = 35


@dataclass(frozen=True)
class ProviderStatus:
    """Outcome of one catalog provider during a discovery pass."""

    name: str
    state: str
    count: int = 0
    message: str = ""
    elapsed_seconds: float = 0.0

    @property
    def display(self) -> str:
        if self.state == "ready":
            return f"{self.name}: {self.count} found"
        if self.state == "empty":
            return f"{self.name}: none"
        return f"{self.name}: {self.state.replace('_', ' ')}"


@dataclass
class DiscoverySnapshot:
    """Ranked results and provider diagnostics reusable by processing."""

    bbox4326: Tuple[float, float, float, float]
    datasets: List[Dict[str, Any]]
    providers: List[ProviderStatus] = field(default_factory=list)
    ranking_mode: str = "best_detail"
    created_at: float = field(default_factory=time.time)

    @property
    def summary(self) -> str:
        return " | ".join(status.display for status in self.providers)


def _usgs_candidates(
    bbox: Tuple[float, float, float, float],
    wesm_records: Dict[str, Dict[str, str]],
    log: LogFunc,
) -> Dict[str, Dict[str, Any]]:
    """Query both TNM product types concurrently and join them to WESM."""
    product_groups: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
    datasets = (
        (usgs.TNM_OPR_DATASET, "source_dem_products"),
        (usgs.TNM_LPC_DATASET, "lpc_products"),
    )
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="tnm") as pool:
        futures = {
            pool.submit(usgs._query_tnm_products, dataset, bbox, log): field_name
            for dataset, field_name in datasets
        }
        for future, field_name in futures.items():
            for product in future.result():
                key = usgs._product_workunit_key(product, wesm_records)
                if key:
                    product_groups.setdefault(key, {}).setdefault(
                        field_name, []
                    ).append(product)

    candidates: Dict[str, Dict[str, Any]] = {}
    for key, grouped in product_groups.items():
        record = wesm_records.get(key) or {}
        workunit = record.get("workunit") or key
        props = _enrich_dataset_properties(
            workunit, {"name": workunit}, wesm_records
        )
        source_products = grouped.get("source_dem_products", [])
        lpc_products = grouped.get("lpc_products", [])
        props.update(grouped)
        props.update(
            {
                "id": workunit,
                "source_type": "source_dem" if source_products else "lpc",
                "covers_aoi": usgs._products_cover_bbox(
                    source_products or lpc_products, bbox
                ),
            }
        )
        candidates[key] = {"id": workunit, "props": props}
    return candidates


def _coastal_candidates(
    bbox: Tuple[float, float, float, float],
    wesm_records: Dict[str, Dict[str, str]],
    log: LogFunc,
):
    records = usiei._query_usiei_topographic(bbox, log=log)
    return (
        records,
        usiei._usiei_candidates(records, bbox, wesm_records, log=log),
        noaa._noaa_candidates(records, bbox, log=log),
    )


def discover_datasets(
    bbox4326: Tuple[float, float, float, float],
    log: LogFunc = None,
    ranking_mode: Optional[str] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> DiscoverySnapshot:
    """Search independent catalogs concurrently and return one reusable snapshot."""
    bbox = tuple(float(value) for value in bbox4326)
    if cancel_check and cancel_check():
        raise RuntimeError("Cancelled by user")

    # WESM is the common metadata join for USGS routes; its own stale-cache
    # fallback means an unavailable refresh does not stop other providers.
    wesm_records = usgs.ensure_wesm_metadata(log=log)
    started = time.monotonic()
    pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="catalog")
    futures = {
        pool.submit(_usgs_candidates, bbox, wesm_records, log): "USGS TNM",
        pool.submit(ept._ept_candidates, bbox, wesm_records, log): "AWS EPT",
        pool.submit(_coastal_candidates, bbox, wesm_records, log): "USIEI",
        pool.submit(opentopography._opentopography_candidates, bbox, log): "OpenTopography",
    }
    done, pending = wait(futures, timeout=DISCOVERY_TIMEOUT_SECONDS)
    pool.shutdown(wait=False, cancel_futures=True)

    outcomes: Dict[str, Any] = {}
    statuses: List[ProviderStatus] = []
    elapsed = time.monotonic() - started
    for future, name in futures.items():
        if future in pending:
            timed_out_names = ("USIEI", "NOAA Coastal") if name == "USIEI" else (name,)
            statuses.extend(
                ProviderStatus(
                    timed_out_name,
                    "timed_out",
                    message="catalog deadline exceeded",
                    elapsed_seconds=elapsed,
                )
                for timed_out_name in timed_out_names
            )
            usgs._log(log, f"{name} timed out after {DISCOVERY_TIMEOUT_SECONDS} seconds")
            continue
        try:
            value = future.result()
            outcomes[name] = value
            if name == "USIEI":
                records, usiei_routes, noaa_routes = value
                statuses.extend(
                    (
                        ProviderStatus(
                            "USIEI",
                            "ready" if records else "empty",
                            count=len(records),
                            elapsed_seconds=elapsed,
                        ),
                        ProviderStatus(
                            "NOAA Coastal",
                            "ready" if noaa_routes else "empty",
                            count=len(noaa_routes),
                            elapsed_seconds=elapsed,
                        ),
                    )
                )
            else:
                count = len(value)
                statuses.append(
                    ProviderStatus(name, "ready" if count else "empty", count=count, elapsed_seconds=elapsed)
                )
        except Exception as exc:
            failed_names = ("USIEI", "NOAA Coastal") if name == "USIEI" else (name,)
            statuses.extend(
                ProviderStatus(
                    failed_name,
                    "unavailable",
                    message=str(exc),
                    elapsed_seconds=elapsed,
                )
                for failed_name in failed_names
            )
            usgs._log(log, f"{name} unavailable ({exc})")

    if cancel_check and cancel_check():
        raise RuntimeError("Cancelled by user")

    candidates: Dict[str, Dict[str, Any]] = dict(outcomes.get("USGS TNM", {}))
    for key, ept_candidate in outcomes.get("AWS EPT", {}).items():
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

    records, usiei_routes, noaa_routes = outcomes.get("USIEI", ([], {}, {}))
    for key, candidate in usiei_routes.items():
        candidates.setdefault(key, candidate)
    usiei._merge_usiei_metadata(candidates, records)

    existing_identities = {
        usiei._project_identity(
            item.get("props", {}).get("workunit")
            or item.get("props", {}).get("id")
            or item.get("id")
        )
        for item in candidates.values()
    }
    for key, candidate in noaa_routes.items():
        props = candidate.get("props", {})
        identity = props.get("usiei_project_identity")
        if identity and identity in existing_identities:
            for existing in candidates.values():
                existing_props = existing.get("props", {})
                existing_identity = usiei._project_identity(
                    existing_props.get("workunit")
                    or existing_props.get("id")
                    or existing.get("id")
                )
                if existing_identity == identity:
                    existing_props["noaa_ept_url"] = props.get("ept_url")
                    existing_props["noaa_mission_id"] = props.get("noaa_mission_id")
                    break
            continue
        candidates[key] = candidate
        if identity:
            existing_identities.add(identity)

    candidates.update(outcomes.get("OpenTopography", {}))
    if ranking_mode is None:
        ranking_mode = str(
            load_config().get("preferences", {}).get("dataset_ranking", "best_detail")
        )
    if ranking_mode not in {"best_detail", "newest"}:
        ranking_mode = "best_detail"
    results = list(candidates.values())
    results.sort(
        key=lambda item: _dataset_sort_key(
            item["id"], item["props"], ranking_mode=ranking_mode
        ),
        reverse=True,
    )
    snapshot = DiscoverySnapshot(bbox, results, statuses, ranking_mode)
    usgs._log(log, f"Catalog discovery: {snapshot.summary}")
    usgs._log(
        log,
        f"Combined discovery found {len(results)} dataset(s); ranking mode: "
        f"{'Best Detail' if ranking_mode == 'best_detail' else 'Newest Survey'}",
    )
    return snapshot


def list_intersecting_datasets(
    bbox4326: Tuple[float, float, float, float],
    log: LogFunc = None,
    ranking_mode: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Compatibility list API backed by concurrent discovery."""
    return discover_datasets(bbox4326, log=log, ranking_mode=ranking_mode).datasets
