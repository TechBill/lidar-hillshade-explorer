"""EPT and official USGS point-cloud delivery."""

from __future__ import annotations

import json
import ssl
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen

try:
    import certifi
    HAS_CERTIFI = True
except ImportError:
    HAS_CERTIFI = False

from utils.config import get_output_dir, load_config
from utils.subprocess_utils import get_creation_flags, get_startupinfo
from lidar_core.catalogs.ept import _get_ept_srs_epsg
from lidar_core.catalogs.opentopography import OPENTOPO_DATA_HOST
from lidar_core.catalogs.usgs import (
    LogFunc,
    _log,
    _product_download_url,
    select_dataset_for_bbox,
)

MIN_LAZ_SIZE = 2000
MIN_POINTS_PER_SQ_MI = 100000


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
    if ds_props.get("source_type") not in {"ept", "noaa_ept", "usiei_ept"} or not ept_url:
        raise RuntimeError("Selected dataset is not available through supported EPT delivery")

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
    request = Request(url, headers={"User-Agent": "LiDAR-Hillshade-Explorer/6.5"})
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
        return True
