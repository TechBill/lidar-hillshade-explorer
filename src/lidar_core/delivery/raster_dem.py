"""Original-resolution raster DEM delivery and cropping."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple
from urllib.parse import unquote, urlparse

from utils.config import get_output_dir
from lidar_core.catalogs.usgs import LogFunc, _log, _product_download_url
from lidar_core.delivery.point_cloud import _download_product_file, _output_basename


def download_source_dem_for_aoi(
    dataset: Dict[str, Any],
    bbox4326: Tuple[float, float, float, float],
    center_lat: float,
    center_lon: float,
    square_miles: float,
    log: LogFunc = None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> Tuple[Path, Dict[str, Any], int, float]:
    """Crop/mosaic an official USGS or public OpenTopography source DTM."""
    import numpy as np
    import rasterio
    from rasterio.merge import merge
    from rasterio.warp import transform_bounds

    props = dict(dataset.get("props") or {})
    products = props.get("source_dem_products") or []
    if not products:
        raise RuntimeError("Selected dataset has no source DEM tiles")

    dem_dir = get_output_dir() / "dem"
    dem_dir.mkdir(parents=True, exist_ok=True)
    dem_path = dem_dir / f"{_output_basename(center_lat, center_lon, square_miles)}.tif"

    is_opentopography = props.get("source_type") == "opentopography_dem"
    source_label = (
        "OpenTopography DTM"
        if is_opentopography
        else "official USGS source DEM"
    )
    _log(log, f"=== Preparing {source_label} ===")
    _log(log, f"Dataset: {props.get('workunit') or dataset.get('id')}")
    with tempfile.TemporaryDirectory(prefix="source_dem_", dir=dem_dir) as temp_dir:
        if is_opentopography:
            _log(log, "Reading public cloud-optimized DTM tiles...")
            sources = [
                rasterio.open(str(_product_download_url(product)))
                for product in products
            ]
        else:
            tile_paths = []
            for index, product in enumerate(products):
                url = _product_download_url(product) or ""
                filename = (
                    Path(unquote(urlparse(url).path)).name or f"tile_{index}.tif"
                )
                tile_paths.append(
                    _download_product_file(
                        product,
                        Path(temp_dir) / filename,
                        log=log,
                        cancel_check=cancel_check,
                    )
                )
            sources = [rasterio.open(path) for path in tile_paths]
        try:
            source_crs = sources[0].crs
            if source_crs is None:
                raise RuntimeError("Source DEM tile has no coordinate system")
            if any(source.crs != source_crs for source in sources[1:]):
                raise RuntimeError("Source DEM tiles use inconsistent coordinate systems")
            projected_bounds = transform_bounds(
                "EPSG:4326", source_crs, *bbox4326, densify_pts=21
            )
            nodata = sources[0].nodata
            if nodata is None:
                nodata = -9999.0
            mosaic, transform = merge(
                sources,
                bounds=projected_bounds,
                nodata=nodata,
                dtype="float32",
            )
            if cancel_check and cancel_check():
                raise RuntimeError("Cancelled by user")
            valid = np.isfinite(mosaic[0]) & (mosaic[0] != nodata)
            if not np.any(valid):
                raise RuntimeError("Source DEM contains no data in the requested area")
            profile = sources[0].profile.copy()
            profile.update(
                driver="GTiff",
                width=mosaic.shape[2],
                height=mosaic.shape[1],
                count=1,
                dtype="float32",
                crs=source_crs,
                transform=transform,
                nodata=nodata,
                compress="LZW",
                tiled=True,
            )
            with rasterio.open(dem_path, "w", **profile) as output:
                output.write(mosaic[0].astype("float32"), 1)
            resolution = (abs(float(transform.a)) + abs(float(transform.e))) / 2.0
            epsg = source_crs.to_epsg() or 0
        finally:
            for source in sources:
                source.close()

    _log(log, f"Source DEM ready: {dem_path.name} ({resolution:g} m)")
    return dem_path, props, int(epsg), resolution
