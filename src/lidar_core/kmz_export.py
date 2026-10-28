#!/usr/bin/env python3
"""
KMZ export for LiDAR Hillshade Explorer.

Converts GeoTIFF hillshades to KMZ format for Google Earth viewing.
"""

from __future__ import annotations

import tempfile
import zipfile
from math import ceil
from pathlib import Path
from typing import Optional
from xml.sax.saxutils import escape


LogFunc = Optional[callable]
KMZ_TILE_SIZE = 2048
KMZ_MAX_TILES = 20


def _log(log: LogFunc, msg: str) -> None:
    """Helper to log message if logger provided."""
    if log:
        log(msg)


def neutral_gray_value(gray, valid) -> int:
    """Choose a visually compatible neutral shade from valid hillshade pixels."""
    import numpy as np

    valid_values = gray[valid]
    if valid_values.size == 0:
        return 180
    return int(np.median(valid_values))


def _hillshade_image(gray, valid, opaque_nodata: bool, nodata_gray: int | None = None):
    """Create a lossless Pillow image, optionally rendering NoData as gray."""
    import numpy as np
    from PIL import Image

    gray = np.asarray(gray, dtype=np.uint8)
    valid = np.asarray(valid, dtype=bool)
    if opaque_nodata:
        rendered = gray.copy()
        rendered[~valid] = (
            neutral_gray_value(gray, valid) if nodata_gray is None else nodata_gray
        )
        return Image.fromarray(rendered, mode="L").convert("RGB")

    image = Image.fromarray(gray, mode="L").convert("RGBA")
    image.putalpha(Image.fromarray((valid.astype(np.uint8) * 255), mode="L"))
    return image


def render_hillshade_image(data, opaque_nodata: bool = False):
    """Render a masked hillshade consistently for the viewer and KMZ export."""
    import numpy as np

    valid = ~np.ma.getmaskarray(data)
    values = np.ma.asarray(data).filled(0)
    if values.dtype == np.uint8:
        gray = values
    elif np.any(valid):
        valid_values = values[valid]
        low = float(valid_values.min())
        high = float(valid_values.max())
        if high > low:
            gray = np.clip(
                (values - low) * 255.0 / (high - low), 0, 255
            ).astype(np.uint8)
        else:
            gray = np.zeros(values.shape, dtype=np.uint8)
    else:
        gray = np.zeros(values.shape, dtype=np.uint8)
    return _hillshade_image(gray, valid, opaque_nodata)


def _tile_grid_dimensions(width: int, height: int) -> tuple[int, int, int]:
    """Return columns, rows, and tile count for a web-compatible KMZ."""
    columns = ceil(width / KMZ_TILE_SIZE)
    rows = ceil(height / KMZ_TILE_SIZE)
    return columns, rows, columns * rows


def _clean_style_name(tif_path: Path) -> str:
    """Return the user-facing hillshade style name from an output filename."""
    tif_name = Path(tif_path).stem
    if "_classic" in tif_name:
        return "Classic"
    if "_preset_" in tif_name:
        return tif_name.split("_preset_")[-1].replace("_", " ")
    if "_arch_" in tif_name:
        return tif_name.split("_arch_")[-1].replace("_", " ")
    if "_custom" in tif_name:
        return "Custom"
    return tif_name


def _create_tiled_overlays(
    tif_path: Path,
    temp_path: Path,
    *,
    opaque_nodata: bool,
    progress_callback: callable = None,
) -> tuple[list[dict], list[Path]]:
    """Render one hillshade as flat 2048-pixel GroundOverlay tiles."""
    import rasterio
    from rasterio.windows import Window
    from rasterio.warp import transform_bounds

    tif_path = Path(tif_path)
    with rasterio.open(tif_path) as source:
        if source.crs is None:
            raise RuntimeError(f"GeoTIFF has no coordinate system: {tif_path}")

        columns, rows, tile_count = _tile_grid_dimensions(
            source.width, source.height
        )
        if tile_count > KMZ_MAX_TILES:
            raise RuntimeError(
                "Google Earth Web KMZ export would require "
                f"{tile_count} image tiles, exceeding the {KMZ_MAX_TILES}-overlay "
                "compatibility limit. Export a smaller area or a lower-resolution "
                "hillshade."
            )

        # Render once so normalization and the opaque NoData shade remain
        # identical across every tile and no seams are introduced.
        image = render_hillshade_image(
            source.read(1, masked=True),
            opaque_nodata=opaque_nodata,
        )
        overlays: list[dict] = []
        png_files: list[Path] = []
        style_name = _clean_style_name(tif_path)
        if progress_callback:
            progress_callback(0, tile_count)

        completed = 0
        for row in range(rows):
            top = row * KMZ_TILE_SIZE
            tile_height = min(KMZ_TILE_SIZE, source.height - top)
            for column in range(columns):
                left = column * KMZ_TILE_SIZE
                tile_width = min(KMZ_TILE_SIZE, source.width - left)
                window = Window(left, top, tile_width, tile_height)
                west, south, east, north = transform_bounds(
                    source.crs,
                    "EPSG:4326",
                    *source.window_bounds(window),
                    densify_pts=21,
                )

                relative_name = f"files/tile_{row:03d}_{column:03d}.png"
                png_path = temp_path / relative_name
                png_path.parent.mkdir(parents=True, exist_ok=True)
                image.crop(
                    (left, top, left + tile_width, top + tile_height)
                ).save(png_path, format="PNG")

                overlays.append(
                    {
                        "name": f"{style_name} tile {completed + 1}",
                        "png_file": relative_name,
                        "north": north,
                        "south": south,
                        "east": east,
                        "west": west,
                    }
                )
                png_files.append(png_path)
                completed += 1
                if progress_callback:
                    progress_callback(completed, tile_count)

    return overlays, png_files


def tif_to_kmz(
    tif_path: Path,
    output_path: Path,
    description: str = "",
    log: LogFunc = None,
    opaque_nodata: bool = False,
    document_name: str | None = None,
    progress_callback: callable = None,
) -> Path:
    """Export one hillshade as a flat, tiled Google Earth Web KMZ."""
    tif_path = Path(tif_path)
    kmz_path = Path(output_path)
    if not tif_path.is_file():
        raise FileNotFoundError(f"Hillshade GeoTIFF does not exist: {tif_path}")
    kmz_path.parent.mkdir(parents=True, exist_ok=True)
    _log(log, f"Creating Google Earth Web KMZ from {tif_path.name}...")

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        overlays, png_files = _create_tiled_overlays(
            tif_path,
            temp_path,
            opaque_nodata=opaque_nodata,
            progress_callback=progress_callback,
        )
        style_name = _clean_style_name(tif_path)
        kml_path = temp_path / "doc.kml"
        kml_name = escape(document_name or style_name)
        kml_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>{kml_name}</name>
"""
        if description:
            kml_content += f"""    <description>{escape(description)}</description>
"""
        kml_content += f"""    <Folder>
      <name>{escape(style_name)}</name>
"""
        for overlay in overlays:
            kml_content += f"""      <GroundOverlay>
      <name>{escape(overlay['name'])}</name>
      <Icon>
        <href>{overlay['png_file']}</href>
      </Icon>
      <LatLonBox>
        <north>{overlay['north']}</north>
        <south>{overlay['south']}</south>
        <east>{overlay['east']}</east>
        <west>{overlay['west']}</west>
      </LatLonBox>
    </GroundOverlay>
"""
        kml_content += """    </Folder>
  </Document>
</kml>"""
        kml_path.write_text(kml_content, encoding="utf-8")

        with zipfile.ZipFile(kmz_path, "w", zipfile.ZIP_DEFLATED) as kmz:
            kmz.write(kml_path, "doc.kml")
            for png_path in png_files:
                kmz.write(png_path, png_path.relative_to(temp_path).as_posix())

    _log(log, f"KMZ created: {kmz_path.name}")
    return kmz_path


def tifs_to_kmz(
    tif_paths: list[Path],
    output_path: Path,
    description: str = "",
    document_name: str = "Hillshade Collection",
    log: LogFunc = None,
    progress_callback: callable = None,
    opaque_nodata: bool = False,
) -> Path:
    """Compatibility wrapper for the former multi-layer export API."""
    if len(tif_paths) != 1:
        raise ValueError(
            "Google Earth Web KMZ export requires exactly one hillshade style"
        )
    return tif_to_kmz(
        tif_paths[0],
        output_path,
        description=description,
        document_name=document_name,
        log=log,
        progress_callback=progress_callback,
        opaque_nodata=opaque_nodata,
    )
