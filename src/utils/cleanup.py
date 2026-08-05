#!/usr/bin/env python3
"""
Cleanup utilities for LiDAR Hillshade Explorer.

Handles deletion of intermediate and temporary files.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional


def cleanup_intermediate_files(laz_path: Optional[Path] = None, dem_path: Optional[Path] = None):
    """
    Delete intermediate files (LAZ and DEM) after hillshade is created.

    Args:
        laz_path: Path to LAZ file to delete
        dem_path: Path to DEM file to delete
    """
    for p in [laz_path, dem_path]:
        if not p:
            continue
        try:
            p = Path(p)
            if p.exists():
                p.unlink()
                print(f"Cleaned up: {p.name}")
        except Exception as e:
            print(f"Warning: could not delete {p}: {e}")


def cleanup_output_folder():
    """
    Delete entire output folder and all its contents.

    Called when the app closes to clean up all temporary files.
    """
    from utils.config import get_output_dir

    output_dir = get_output_dir()
    if output_dir.exists():
        try:
            shutil.rmtree(output_dir, ignore_errors=True)
            print(f"Cleaned up output directory: {output_dir}")
        except Exception as e:
            print(f"Warning: could not clean output directory: {e}")
