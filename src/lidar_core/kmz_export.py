#!/usr/bin/env python3
"""
KMZ export for LiDAR Hillshade Explorer.

Converts GeoTIFF hillshades to KMZ format for Google Earth viewing.
"""

from __future__ import annotations

import tempfile
import zipfile
from pathlib import Path
from typing import Optional


LogFunc = Optional[callable]


def _log(log: LogFunc, msg: str) -> None:
    """Helper to log message if logger provided."""
    if log:
        log(msg)


def _tif_to_png_and_bounds(tif_path: Path, png_path: Path) -> dict:
    """Render a GeoTIFF to PNG and return its WGS84 overlay bounds."""
    import numpy as np
    import rasterio
    from PIL import Image
    from rasterio.warp import transform_bounds

    with rasterio.open(tif_path) as src:
        data = src.read(1, masked=True)
        if src.crs is None:
            raise RuntimeError(f"GeoTIFF has no coordinate system: {tif_path}")

        west, south, east, north = transform_bounds(
            src.crs, "EPSG:4326", *src.bounds, densify_pts=21
        )

        valid = ~np.ma.getmaskarray(data)
        values = data.filled(0)
        if values.dtype == np.uint8:
            gray = values
        elif np.any(valid):
            valid_values = values[valid]
            low = float(valid_values.min())
            high = float(valid_values.max())
            if high > low:
                gray = np.clip((values - low) * 255.0 / (high - low), 0, 255).astype(np.uint8)
            else:
                gray = np.zeros(values.shape, dtype=np.uint8)
        else:
            gray = np.zeros(values.shape, dtype=np.uint8)

        image = Image.fromarray(gray, mode="L").convert("RGBA")
        image.putalpha(Image.fromarray((valid.astype(np.uint8) * 255), mode="L"))
        image.save(png_path, format="PNG")

    return {
        "north": north,
        "south": south,
        "east": east,
        "west": west,
    }


def tif_to_kmz(
    tif_path: Path,
    output_path: Path,
    description: str = "",
    log: LogFunc = None
) -> Path:
    """
    Convert GeoTIFF to KMZ format for Google Earth.

    Args:
        tif_path: Path to input GeoTIFF file
        output_path: Full path where KMZ file will be saved (including filename)
        description: Custom description for the KML document (optional)
        log: Optional logging function

    Returns:
        Path to generated KMZ file

    Raises:
        RuntimeError if GDAL not available or conversion fails
    """
    tif_path = Path(tif_path)
    kmz_path = Path(output_path)

    # Ensure parent directory exists
    kmz_path.parent.mkdir(parents=True, exist_ok=True)

    _log(log, f"Converting {tif_path.name} to KMZ...")

    # Create temporary directory for intermediate files
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)

        # Step 1: Convert GeoTIFF to PNG with GDAL
        png_path = temp_path / (tif_path.stem + ".png")
        _log(log, f"Creating PNG...")

        bounds = _tif_to_png_and_bounds(tif_path, png_path)
        _log(
            log,
            f"WGS84 extent: {bounds['south']:.6f},{bounds['west']:.6f} "
            f"to {bounds['north']:.6f},{bounds['east']:.6f}",
        )

        # Step 2: Create KML file
        kml_path = temp_path / "doc.kml"
        _log(log, f"Creating KML...")

        # Build KML with optional description
        kml_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>{tif_path.stem}</name>
"""

        # Only add description if provided
        if description:
            kml_content += f"""    <description>{description}</description>
"""

        kml_content += f"""    <GroundOverlay>
      <name>{tif_path.stem}</name>
      <Icon>
        <href>{png_path.name}</href>
      </Icon>
      <LatLonBox>
        <north>{bounds['north']}</north>
        <south>{bounds['south']}</south>
        <east>{bounds['east']}</east>
        <west>{bounds['west']}</west>
      </LatLonBox>
    </GroundOverlay>
  </Document>
</kml>"""

        with open(kml_path, 'w', encoding='utf-8') as f:
            f.write(kml_content)

        # Step 3: Create KMZ (ZIP with KML + PNG)
        _log(log, f"Creating KMZ...")

        with zipfile.ZipFile(kmz_path, 'w', zipfile.ZIP_DEFLATED) as kmz:
            kmz.write(kml_path, 'doc.kml')
            kmz.write(png_path, png_path.name)

    _log(log, f"KMZ created: {kmz_path.name}")
    return kmz_path


def tifs_to_kmz(
    tif_paths: list[Path],
    output_path: Path,
    description: str = "",
    document_name: str = "Hillshade Collection",
    log: LogFunc = None,
    progress_callback: callable = None
) -> Path:
    """
    Convert multiple GeoTIFF files to a single KMZ with multiple layers.

    Args:
        tif_paths: List of paths to input GeoTIFF files
        output_path: Full path where KMZ file will be saved (including filename)
        description: Custom description for the KML document (optional)
        document_name: Name for the KML document (optional, defaults to "Hillshade Collection")
        log: Optional logging function
        progress_callback: Optional callback(current, total) for progress updates

    Returns:
        Path to generated KMZ file

    Raises:
        RuntimeError if GDAL not available or conversion fails
    """
    if not tif_paths:
        raise ValueError("No TIF files provided")

    kmz_path = Path(output_path)
    kmz_path.parent.mkdir(parents=True, exist_ok=True)

    _log(log, f"Converting {len(tif_paths)} TIF files to single KMZ...")

    # Create temporary directory for intermediate files
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)

        # List to store ground overlays info
        overlays = []
        png_files = []

        # Process each TIF file
        total_files = len(tif_paths)
        for idx, tif_path in enumerate(tif_paths):
            if progress_callback:
                progress_callback(idx, total_files)

            tif_path = Path(tif_path)
            _log(log, f"Processing {tif_path.name} ({idx + 1}/{total_files})...")

            # Step 1: Convert GeoTIFF to PNG
            # Extract clean name from TIF filename (remove prefix, year, _dem_arch_)
            tif_name = tif_path.stem

            # Extract just the hillshade type part
            if '_classic' in tif_name:
                clean_name = "Classic"
            elif '_arch_' in tif_name:
                # Extract everything after _arch_
                clean_name = tif_name.split('_arch_')[-1].replace('_', ' ')
            elif '_custom' in tif_name:
                clean_name = "Custom"
            else:
                # Fallback to using the stem
                clean_name = tif_name

            png_path = temp_path / f"{clean_name}.png"

            try:
                bounds = _tif_to_png_and_bounds(tif_path, png_path)
            except Exception as exc:
                _log(log, f"Warning: Failed to open {tif_path.name} ({exc}), skipping...")
                continue

            # Store overlay info
            overlays.append({
                'name': clean_name,
                'png_file': png_path.name,
                **bounds,
            })
            png_files.append(png_path)

        if not overlays:
            raise RuntimeError("No valid TIF files could be processed")

        # Step 2: Create KML with multiple GroundOverlays
        kml_path = temp_path / "doc.kml"
        _log(log, f"Creating KML with {len(overlays)} layers...")

        # Use custom description only if provided (don't auto-generate)
        kml_description = description if description else ""

        # Build KML with multiple overlays
        kml_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>{document_name}</name>
"""

        # Only add description if provided
        if kml_description:
            kml_content += f"""    <description>{kml_description}</description>
"""

        # Add each overlay as a separate GroundOverlay
        for overlay in overlays:
            kml_content += f"""    <GroundOverlay>
      <name>{overlay['name']}</name>
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

        kml_content += """  </Document>
</kml>"""

        with open(kml_path, 'w', encoding='utf-8') as f:
            f.write(kml_content)

        # Step 3: Create KMZ (ZIP with KML + all PNGs)
        _log(log, f"Creating KMZ...")

        with zipfile.ZipFile(kmz_path, 'w', zipfile.ZIP_DEFLATED) as kmz:
            kmz.write(kml_path, 'doc.kml')
            for png_path in png_files:
                kmz.write(png_path, png_path.name)

        if progress_callback:
            progress_callback(total_files, total_files)

    _log(log, f"KMZ created with {len(overlays)} layers: {kmz_path.name}")
    return kmz_path
