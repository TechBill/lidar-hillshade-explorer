#!/usr/bin/env python3
"""
DEM generation for LiDAR Hillshade Explorer.

Creates ground-only DEMs from LAZ/LAS files using the standard PDAL CLI
(no pdal_wrench dependency).
"""

from __future__ import annotations

import json
import math
import re
import subprocess
from pathlib import Path
from typing import Optional

from utils.config import (
    TERRAIN_STYLE_LABELS,
    get_effective_dem_settings,
    load_config,
    get_output_dir,
)
from utils.subprocess_utils import get_creation_flags, get_startupinfo


LogFunc = Optional[callable]


def _log(log: LogFunc, msg: str):
    """Helper to log message if logger provided."""
    if log:
        log(msg)


def _clamp_int(value: int, min_v: int, max_v: int) -> int:
    return max(min_v, min(max_v, value))


def _clamp_float(value: float, min_v: float, max_v: float) -> float:
    return max(min_v, min(max_v, value))


def auto_resolution_for_file(lidar_path: Path, log: LogFunc = None) -> tuple[float, float]:
    """
    Analyze LiDAR point spacing and select optimal DEM resolution.

    Uses `pdal info --all` to get avg_pt_spacing:
    - avg_pt_spacing < 0.30m → 0.25m DEM
    - avg_pt_spacing < 0.75m → 0.5m DEM
    - otherwise → 1.0m DEM

    Args:
        lidar_path: Path to LAZ/LAS file
        log: Optional logging function

    Returns:
        Tuple of (point_spacing, dem_resolution) in meters
    """
    _log(log, "Analyzing LiDAR point spacing...")

    try:
        from .aws_operations import find_pdal
        config = load_config()
        pdal_path = find_pdal(config)
    except Exception as e:
        _log(log, f"Warning: Could not find PDAL ({e}), defaulting to 1.0m resolution")
        return (1.0, 1.0)  # Return (spacing, resolution)

    from utils.binary_paths import get_bundled_lib_env
    env = get_bundled_lib_env()
    cmd = [pdal_path, "info", "--all", str(lidar_path)]
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env,
                          creationflags=get_creation_flags(),
                          startupinfo=get_startupinfo())

    if proc.returncode != 0:
        _log(log, "Warning: Could not analyze point spacing, defaulting to 1.0m resolution")
        return (1.0, 1.0)  # Return (spacing, resolution)

    # Parse output for avg_pt_spacing
    spacing = None
    for line in proc.stdout.splitlines():
        if '"avg_pt_spacing"' in line:
            part = line.split(":", 1)[1]
            s = re.sub(r"[^0-9.]", "", part)
            if s:
                try:
                    spacing = float(s)
                    break
                except ValueError:
                    pass

    # Select resolution based on spacing
    if spacing is None:
        _log(log, "Could not determine point spacing, using 1.0m resolution")
        return (1.0, 1.0)  # Return (spacing, resolution)

    if spacing < 0.30:
        dem_res = 0.25
    elif spacing < 0.75:
        dem_res = 0.5
    else:
        dem_res = 1.0

    _log(log, f"Point spacing: {spacing:.3f}m → DEM resolution: {dem_res}m")
    return (spacing, dem_res)


def _fill_dem_nodata(
    dem_path: Path,
    max_search_dist: float,
    smoothing: int,
    log: LogFunc = None
) -> None:
    """Fill small DEM NoData holes in-place using Rasterio/GDAL."""
    try:
        import rasterio
        from rasterio.fill import fillnodata
    except Exception as e:
        _log(log, f"Warning: Rasterio fill support not available ({e}); skipping DEM fill")
        return

    try:
        _log(log, f"Filling DEM nodata (max_search_dist={max_search_dist}, smoothing={smoothing})...")
        with rasterio.open(dem_path, "r+") as ds:
            data = ds.read(1)
            valid_mask = ds.read_masks(1)
            filled = fillnodata(
                data,
                mask=valid_mask,
                max_search_distance=max_search_dist,
                smoothing_iterations=smoothing,
            )
            ds.write(filled, 1)
    except Exception as e:
        _log(log, f"Warning: DEM fill failed ({e}); leaving original NoData cells")


def _run_pdal_pipeline(
    pipeline_obj: dict,
    pipeline_path: Path,
    env: dict,
    pdal_path: str,
    log: LogFunc,
    cancel_check: Optional[callable],
    error_prefix: str = "DEM generation"
) -> None:
    """Write pipeline JSON and run it via PDAL CLI with cancel support."""
    import time

    pipeline_path.write_text(json.dumps(pipeline_obj, indent=2), encoding="utf-8")
    cmd = [pdal_path, "pipeline", str(pipeline_path)]
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
        creationflags=get_creation_flags(), startupinfo=get_startupinfo()
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
        raise RuntimeError(f"{error_prefix} failed:\n{stderr or stdout}")


def _projected_raster_grid(
    bbox4326: tuple[float, float, float, float],
    epsg_code: int,
    dem_res: float,
) -> dict:
    """Return a FaceRaster grid covering the exact requested AOI."""
    from pyproj import Transformer

    lon_min, lat_min, lon_max, lat_max = bbox4326
    transformer = Transformer.from_crs(
        "EPSG:4326", f"EPSG:{epsg_code}", always_xy=True
    )
    corners = [
        transformer.transform(lon_min, lat_min),
        transformer.transform(lon_min, lat_max),
        transformer.transform(lon_max, lat_min),
        transformer.transform(lon_max, lat_max),
    ]
    xs = [point[0] for point in corners]
    ys = [point[1] for point in corners]
    minx, maxx = min(xs), max(xs)
    miny, maxy = min(ys), max(ys)

    return {
        "origin_x": minx,
        "origin_y": miny,
        "width": max(1, math.ceil((maxx - minx) / dem_res)),
        "height": max(1, math.ceil((maxy - miny) / dem_res)),
    }


def _build_tin_pipeline(
    lidar_path: Path,
    dem_path: Path,
    dem_res: float,
    max_triangle_edge: float,
    raster_grid: Optional[dict] = None,
) -> dict:
    """
    Build a TIN-based DEM pipeline using Delaunay triangulation.

    Covers the full convex hull of ground points with no voids — every raster
    cell inside a triangle gets an interpolated Z value, eliminating the black
    patches that IDW can leave where point density is low.

    Requires PDAL 2.3+ (filters.delaunay + filters.faceraster).
    """
    faceraster = {
        "type": "filters.faceraster",
        "resolution": dem_res,
        "max_triangle_edge_length": max_triangle_edge,
    }
    if raster_grid:
        faceraster.update(raster_grid)

    return {
        "pipeline": [
            {"type": "readers.las", "filename": str(lidar_path)},
            {"type": "filters.range", "limits": "Classification[2:2]"},
            {"type": "filters.delaunay"},
            faceraster,
            {
                "type": "writers.raster",
                "filename": str(dem_path),
                "gdaldriver": "GTiff",
                "data_type": "float",
                "nodata": -9999.0,
                "gdalopts": "COMPRESS=LZW,TILED=YES",
            },
        ]
    }


def _dem_nodata_percentage(dem_path: Path, log: LogFunc = None) -> Optional[float]:
    """Measure DEM NoData coverage in blocks without loading it all at once."""
    try:
        import numpy as np
        import rasterio

        nodata_count = 0
        cell_count = 0
        with rasterio.open(dem_path) as src:
            for _, window in src.block_windows(1):
                data = src.read(1, window=window, masked=True)
                nodata_count += int(np.count_nonzero(np.ma.getmaskarray(data)))
                cell_count += data.size
        if not cell_count:
            return None
        return 100.0 * nodata_count / cell_count
    except Exception as exc:
        _log(log, f"Warning: Could not measure DEM NoData coverage ({exc})")
        return None


def _build_idw_pipeline(
    lidar_path: Path, dem_path: Path, dem_res: float, window_size: int
) -> dict:
    """Build a fallback IDW-based DEM pipeline."""
    return {
        "pipeline": [
            {"type": "readers.las", "filename": str(lidar_path)},
            {"type": "filters.range", "limits": "Classification[2:2]"},
            {
                "type": "writers.gdal",
                "filename": str(dem_path),
                "gdaldriver": "GTiff",
                "resolution": dem_res,
                "output_type": "idw",
                "window_size": window_size,
                "data_type": "float32",
                "nodata": -9999,
                "gdalopts": "COMPRESS=LZW,TILED=YES",
            },
        ]
    }


def run_tin_dem(
    lidar_path: Path,
    aoi_bbox4326: Optional[tuple[float, float, float, float]] = None,
    epsg_code: Optional[int] = None,
    log: LogFunc = None,
    cancel_check: Optional[callable] = None
) -> tuple[Path, float, float]:
    """
    Create ground-only DEM from LAZ/LAS file.

    Attempts TIN (Delaunay triangulation via filters.delaunay + filters.faceraster)
    which produces void-free results within the point cloud footprint. Falls back
    to IDW rasterization if the installed PDAL version does not support TIN filters.

    Args:
        lidar_path: Path to input LAZ/LAS file
        log: Optional logging function
        cancel_check: Optional function that returns True if cancelled

    Returns:
        Tuple of (dem_path, point_spacing, dem_resolution)

    Raises:
        RuntimeError if DEM creation fails or cancelled
    """
    output_dir = get_output_dir()
    dem_dir = output_dir / "dem"
    dem_dir.mkdir(parents=True, exist_ok=True)

    base = lidar_path.stem
    if base.endswith("_ground"):
        base = base[:-7]
    dem_filename = f"{base}.tif"
    dem_path = dem_dir / dem_filename

    _log(log, "=== Creating DEM ===")
    _log(log, f"Input: {lidar_path.name}")
    _log(log, f"Output: {dem_filename}")

    point_spacing, dem_res = auto_resolution_for_file(lidar_path, log=log)

    config = load_config()
    try:
        from .aws_operations import find_pdal
        pdal_path = find_pdal(config)
    except FileNotFoundError as e:
        raise RuntimeError(str(e))

    _log(log, f"Using PDAL: {Path(pdal_path).name}")

    dem_fill = get_effective_dem_settings(config)
    terrain_style = dem_fill["terrain_style"]
    _log(log, f"Terrain style: {TERRAIN_STYLE_LABELS[terrain_style]}")
    window_size = _clamp_int(int(dem_fill.get("idw_window_size", 12)), 3, 32)
    max_search = _clamp_int(int(dem_fill.get("fill_max_search", 16)), 3, 64)
    smoothing = _clamp_int(int(dem_fill.get("fill_smoothing", 4)), 0, 10)
    deterministic = bool(dem_fill.get("deterministic", False))
    try:
        edge_multiplier = float(dem_fill.get("tin_max_edge_multiplier", 12))
    except (TypeError, ValueError):
        edge_multiplier = 12.0
    edge_multiplier = _clamp_float(edge_multiplier, 4.0, 40.0)
    max_triangle_edge = _clamp_float(
        point_spacing * edge_multiplier, 6.0, 40.0
    )

    raster_grid = None
    if aoi_bbox4326 is not None and epsg_code is not None:
        raster_grid = _projected_raster_grid(aoi_bbox4326, epsg_code, dem_res)
        _log(
            log,
            "TIN output grid: "
            f"{raster_grid['width']} x {raster_grid['height']} cells "
            "(cropped to requested area)",
        )

    from utils.binary_paths import get_bundled_lib_env
    env = get_bundled_lib_env()
    if deterministic:
        env["GDAL_NUM_THREADS"] = "1"

    # Try TIN first; fall back to IDW if filters.delaunay/faceraster unavailable
    tin_pipeline = _build_tin_pipeline(
        lidar_path,
        dem_path,
        dem_res,
        max_triangle_edge=max_triangle_edge,
        raster_grid=raster_grid,
    )
    tin_pipeline_path = dem_dir / f"{base}_tin_pipeline.json"

    _log(
        log,
        f"Attempting adaptive TIN DEM (resolution={dem_res}m, "
        f"max triangle edge={max_triangle_edge:.2f}m)...",
    )
    try:
        _run_pdal_pipeline(
            tin_pipeline, tin_pipeline_path, env, pdal_path, log, cancel_check
        )
        _log(log, "TIN DEM succeeded.")
    except RuntimeError as e:
        err = str(e)
        if "Cancelled by user" in err:
            raise
        _log(log, f"TIN pipeline failed ({err.splitlines()[0]}), falling back to IDW...")
        idw_pipeline = _build_idw_pipeline(lidar_path, dem_path, dem_res, window_size)
        idw_pipeline_path = dem_dir / f"{base}_idw_pipeline.json"
        _log(log, f"IDW DEM settings: window={window_size}")
        _run_pdal_pipeline(
            idw_pipeline, idw_pipeline_path, env, pdal_path, log, cancel_check,
            error_prefix="DEM generation (IDW fallback)"
        )
        _log(log, "IDW DEM succeeded.")

    if not dem_path.is_file():
        raise RuntimeError(f"DEM file was not created: {dem_path}")

    _log(log, f"DEM created: {dem_path.stat().st_size:,} bytes")

    before_fill = _dem_nodata_percentage(dem_path, log=log)
    if before_fill is not None:
        _log(log, f"TIN NoData before small-gap fill: {before_fill:.2f}%")

    # Fill any remaining edge voids (corners outside TIN hull, water bodies, etc.)
    _log(log, f"Filling edge voids (max_search={max_search}, smoothing={smoothing})...")
    _fill_dem_nodata(dem_path, max_search_dist=max_search, smoothing=smoothing, log=log)

    after_fill = _dem_nodata_percentage(dem_path, log=log)
    if after_fill is not None:
        _log(log, f"Final DEM NoData: {after_fill:.2f}%")
        if after_fill > 5.0:
            _log(
                log,
                "Warning: More than 5% of the requested area has no reliable "
                "ground coverage.",
            )

    return (dem_path, point_spacing, dem_res)
