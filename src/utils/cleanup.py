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
    Delete only this app process's session output and all its contents.

    Called when the viewer or app closes. Other app instances use sibling
    session directories and must remain untouched.
    """
    from utils.config import get_output_dir

    output_dir = get_output_dir()
    if output_dir.exists():
        try:
            shutil.rmtree(output_dir, ignore_errors=True)
            print(f"Cleaned up session output directory: {output_dir}")
            try:
                output_dir.parent.rmdir()
            except OSError:
                pass
        except Exception as e:
            print(f"Warning: could not clean output directory: {e}")
