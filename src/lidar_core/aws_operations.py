#!/usr/bin/env python3
"""
AWS/USGS LiDAR operations for LiDAR Hillshade Explorer.

Handles dataset discovery, selection, and download/clipping using PDAL.
Combined functionality from LidarStudio's aws_index, aws_select_dataset, and aws_clip_class2.
"""

from __future__ import annotations

import csv
import io
import json
import math
import re
import subprocess
import time
import ssl
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional
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
    "https://rockyweb.usgs.gov/vdelivery/Datasets/Staged/Elevation/"
    "metadata/WESM.csv"
)

CACHE_EXPIRY_DAYS = 30
WESM_CACHE_EXPIRY_DAYS = 7
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
        if not records:
            raise ValueError("USGS metadata file contained no work units")

        temp_path = cache_path.with_suffix(".csv.tmp")
        temp_path.write_text(csv_text, encoding="utf-8")
        temp_path.replace(cache_path)
        _log(log, f"Cached metadata for {len(records):,} USGS work units")
        return records
    except Exception as exc:
        if cache_path.is_file():
            try:
                records = _parse_wesm_csv(cache_path.read_text(encoding="utf-8-sig"))
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


def _dataset_sort_key(dataset_id: str, props: Dict[str, Any]) -> tuple[int, int, str]:
    """Sort by actual collection end date, then quality, then stable ID."""
    date_text = str(props.get("collection_end") or "").replace("-", "")
    if len(date_text) == 8 and date_text.isdigit():
        date_value = int(date_text)
    else:
        year = props.get("collection_year")
        date_value = int(year) * 10000 if isinstance(year, int) else 19000000
    quality = props.get("quality_level")
    quality_score = -int(quality) if isinstance(quality, int) else -999
    return (date_value, quality_score, dataset_id)


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


def list_intersecting_datasets(
    bbox4326: Tuple[float, float, float, float],
    log: LogFunc = None
) -> List[Dict[str, Any]]:
    """
    Return all AWS/USGS LiDAR datasets that intersect the bbox, sorted by ID.

    Args:
        bbox4326: Bounding box (lon_min, lat_min, lon_max, lat_max) in EPSG:4326
        log: Optional logging function

    Returns:
        List of dicts with keys: id, props
    """
    index = ensure_aws_index(log=log)
    features: List[Dict[str, Any]] = index.get("features", [])

    candidates: List[Tuple[str, Dict[str, Any]]] = []
    wesm_records = ensure_wesm_metadata(log=log)

    lon_min, lat_min, lon_max, lat_max = bbox4326
    aoi_bbox = (float(lon_min), float(lat_min), float(lon_max), float(lat_max))

    for feat in features:
        geom = feat.get("geometry") or {}
        props = feat.get("properties") or {}

        # Get feature bbox
        fbbox_raw = feat.get("bbox")
        if isinstance(fbbox_raw, (list, tuple)) and len(fbbox_raw) >= 4:
            f_minx, f_miny, f_maxx, f_maxy = fbbox_raw[:4]
            fbbox = (float(f_minx), float(f_miny), float(f_maxx), float(f_maxy))
        else:
            try:
                fbbox = _bbox_from_geometry(geom)
            except Exception:
                continue

        if not _bbox_intersects(aoi_bbox, fbbox):
            continue

        ds_id = _extract_dataset_id(props)
        if not ds_id:
            continue

        enriched_props = _enrich_dataset_properties(ds_id, props, wesm_records)
        candidates.append((ds_id, enriched_props))

    if not candidates:
        return []

    # Prefer authoritative acquisition date. Quality level is a tie-breaker
    # because a lower QL number represents denser/higher-quality lidar.
    candidates.sort(
        key=lambda item: _dataset_sort_key(item[0], item[1]), reverse=True
    )

    return [
        {"id": ds_id, "props": dict(props)}
        for ds_id, props in candidates
    ]


def select_dataset_for_bbox(
    bbox4326: Tuple[float, float, float, float],
    rank: int = 0,
    log: LogFunc = None
) -> Tuple[str, int, Dict[str, Any]]:
    """
    Select the Nth-newest intersecting dataset for the AOI.

    Args:
        bbox4326: Bounding box (lon_min, lat_min, lon_max, lat_max) in EPSG:4326
        rank: 0=newest, 1=second-newest, etc.
        log: Optional logging function

    Returns:
        (ept_url, epsg_code, dataset_properties)

    Raises:
        RuntimeError if no datasets intersect or rank out of range
    """
    if rank < 0:
        rank = 0

    candidates = list_intersecting_datasets(bbox4326=bbox4326, log=log)

    if not candidates:
        raise RuntimeError(
            "No USGS/AWS LiDAR datasets found for this location. "
            "Try a different area within the United States."
        )

    if rank >= len(candidates):
        raise RuntimeError(
            f"Requested dataset rank {rank} but only {len(candidates)} "
            "dataset(s) available."
        )

    chosen = candidates[rank]
    best_id = chosen["id"]
    best_props = chosen["props"]

    # Build EPT URL
    ept_url = (
        f"https://s3-us-west-2.amazonaws.com/usgs-lidar-public/{best_id}/ept.json"
    )

    # Get EPSG from EPT
    epsg = _get_ept_srs_epsg(ept_url)

    best_props = dict(best_props)
    best_props["index_id"] = best_props.get("id")
    best_props["id"] = best_id

    _log(log, f"Selected dataset: {best_id}")
    _log(log, f"EPT URL: {ept_url}")
    _log(log, f"EPSG: {epsg}")
    if best_props.get("collection_year"):
        suffix = " (estimated)" if best_props.get("year_estimated") else ""
        details = f"Collected: {best_props['collection_year']}{suffix}"
        if best_props.get("quality_level"):
            details += f", QL{best_props['quality_level']}"
        _log(log, details)

    return ept_url, epsg, best_props


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
        dataset_rank: 0=newest, 1=second-newest, etc.
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

    # Select dataset
    ept_url, epsg_code, ds_props = select_dataset_for_bbox(
        bbox4326=bbox4326,
        rank=dataset_rank,
        log=log
    )

    ds_id = ds_props.get("id") or "unknown"

    # Build output filename (simplified - no AOI prefix or year)
    lat_str = f"{float(center_lat):.4f}"
    lon_str = f"{float(center_lon):.4f}"
    sqmi_str = f"{square_miles:.2f}".rstrip("0").rstrip(".")

    clipped_name = f"{lat_str}_{lon_str}_{sqmi_str}sqmi_ground.laz"
    clipped_laz = laz_dir / clipped_name

    _log(log, f"Output: {clipped_name}")

    # Convert bbox from EPSG:4326 to dataset CRS for proper clipping
    # Set up pyproj to use Homebrew's PROJ data before importing
    import os
    from pathlib import Path
    for proj_path in ["/opt/homebrew/share/proj", "/usr/local/share/proj"]:
        if Path(proj_path).exists():
            os.environ["PROJ_LIB"] = proj_path
            os.environ["PROJ_DATA"] = proj_path
            break
    
    from pyproj import Transformer
    import pyproj.datadir
    # Set pyproj's data directory to use Homebrew's PROJ data
    for proj_path in ["/opt/homebrew/share/proj", "/usr/local/share/proj"]:
        if Path(proj_path).exists():
            pyproj.datadir.set_data_dir(proj_path)
            break

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
