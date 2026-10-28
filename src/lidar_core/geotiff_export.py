"""Lossless Cloud Optimized GeoTIFF export and validation."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Tuple

import rasterio
from rasterio.shutil import copy as raster_copy


COG_CREATION_OPTIONS = {
    "COMPRESS": "DEFLATE",
    "PREDICTOR": "YES",
    "BLOCKSIZE": "512",
    "OVERVIEW_RESAMPLING": "AVERAGE",
    "BIGTIFF": "IF_SAFER",
}


@dataclass(frozen=True)
class CogValidation:
    """Verified structural details of an exported COG."""

    layout: str
    block_shapes: Tuple[Tuple[int, int], ...]
    overview_levels: Tuple[int, ...]
    checksums: Tuple[int, ...]


def _assert_equal(label: str, source_value, exported_value) -> None:
    if source_value != exported_value:
        raise RuntimeError(
            f"COG validation failed: {label} changed "
            f"({source_value!r} != {exported_value!r})"
        )


def validate_cog(source_path: Path, cog_path: Path) -> CogValidation:
    """Verify COG structure and lossless preservation of the source raster."""
    source_path = Path(source_path)
    cog_path = Path(cog_path)

    with rasterio.open(source_path) as source, rasterio.open(cog_path) as exported:
        image_structure = exported.tags(ns="IMAGE_STRUCTURE")
        layout = image_structure.get("LAYOUT", "")
        if layout != "COG":
            raise RuntimeError(
                f"COG validation failed: expected LAYOUT=COG, found {layout or 'none'}"
            )

        block_shapes = tuple(exported.block_shapes)
        if not block_shapes or any(shape != (512, 512) for shape in block_shapes):
            raise RuntimeError(
                f"COG validation failed: expected 512 x 512 blocks, found {block_shapes}"
            )

        overview_levels = tuple(exported.overviews(1)) if exported.count else ()
        # GDAL's automatic COG pyramid intentionally omits overviews when the
        # full raster already fits within one 512-pixel tile.
        if not overview_levels and max(exported.width, exported.height) > 512:
            raise RuntimeError("COG validation failed: no internal overviews were created")

        _assert_equal("CRS", source.crs, exported.crs)
        _assert_equal("transform", source.transform, exported.transform)
        _assert_equal(
            "dimensions",
            (source.width, source.height),
            (exported.width, exported.height),
        )
        _assert_equal("band count", source.count, exported.count)
        _assert_equal("band types", source.dtypes, exported.dtypes)
        _assert_equal("NoData values", source.nodatavals, exported.nodatavals)
        _assert_equal("color interpretation", source.colorinterp, exported.colorinterp)
        _assert_equal("band descriptions", source.descriptions, exported.descriptions)
        _assert_equal("band scales", source.scales, exported.scales)
        _assert_equal("band offsets", source.offsets, exported.offsets)
        _assert_equal("band units", source.units, exported.units)

        source_tags = source.tags()
        exported_tags = exported.tags()
        for key, value in source_tags.items():
            if exported_tags.get(key) != value:
                raise RuntimeError(
                    f"COG validation failed: dataset metadata {key!r} changed"
                )
        for band in range(1, source.count + 1):
            source_band_tags = source.tags(band)
            exported_band_tags = exported.tags(band)
            for key, value in source_band_tags.items():
                if exported_band_tags.get(key) != value:
                    raise RuntimeError(
                        f"COG validation failed: band {band} metadata {key!r} changed"
                    )

        source_checksums = tuple(source.checksum(band) for band in source.indexes)
        exported_checksums = tuple(
            exported.checksum(band) for band in exported.indexes
        )
        _assert_equal(
            "full-resolution band checksums",
            source_checksums,
            exported_checksums,
        )

        if image_structure.get("COMPRESSION") != "DEFLATE":
            raise RuntimeError("COG validation failed: DEFLATE compression is not active")
        if image_structure.get("PREDICTOR") != "2":
            raise RuntimeError(
                "COG validation failed: horizontal differencing predictor is not active"
            )

    if cog_path.with_suffix(cog_path.suffix + ".ovr").exists():
        raise RuntimeError("COG validation failed: overviews were written externally")

    return CogValidation(layout, block_shapes, overview_levels, exported_checksums)


def export_cog(source_path: Path, destination_path: Path) -> CogValidation:
    """Create and validate a lossless COG, replacing the destination atomically."""
    source_path = Path(source_path)
    destination_path = Path(destination_path)
    if not source_path.is_file():
        raise FileNotFoundError(f"Source GeoTIFF does not exist: {source_path}")
    destination_path.parent.mkdir(parents=True, exist_ok=True)

    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination_path.stem}_",
        suffix=".tif",
        dir=destination_path.parent,
    )
    os.close(file_descriptor)
    temporary_path = Path(temporary_name)
    temporary_path.unlink()

    try:
        raster_copy(
            source_path,
            temporary_path,
            driver="COG",
            strict=True,
            **COG_CREATION_OPTIONS,
        )
        validation = validate_cog(source_path, temporary_path)
        temporary_path.replace(destination_path)
        return validation
    finally:
        temporary_path.unlink(missing_ok=True)
