#!/usr/bin/env python3
"""
Processing orchestrator for LiDAR Hillshade Explorer.

Coordinates the complete workflow from user input to hillshade generation.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Dict, Callable, Optional

from lidar_core.aws_operations import (
    clip_aws_aoi_to_ground_laz,
    download_lpc_for_aoi,
    download_source_dem_for_aoi,
    validate_laz_point_density,
    list_intersecting_datasets,
)
from lidar_core.dem_generator import run_tin_dem
from lidar_core.hillshade_engine import generate_classic_hillshade


class ProcessingOrchestrator:
    """
    Orchestrates the LiDAR-to-hillshade workflow.

    Steps:
    1. Compute AOI bbox from center coordinates + size
    2. Find and download LiDAR dataset (with smart selection/fallback)
    3. Generate TIN DEM at auto-resolution
    4. Create classic hillshade
    5. Return paths and metadata
    """

    def __init__(self):
        self.log_messages = []

    def get_available_datasets(
        self,
        lat: float,
        lon: float,
        size_sqmi: float
    ) -> list[dict]:
        """
        Get list of all available datasets for the location.

        Args:
            lat: Center latitude
            lon: Center longitude
            size_sqmi: Area size in square miles

        Returns:
            List of dataset dictionaries with keys: id, props
        """
        bbox = self._compute_bbox(lat, lon, size_sqmi)
        return list_intersecting_datasets(bbox4326=bbox)

    def run_workflow(
        self,
        lat: float,
        lon: float,
        size_sqmi: float,
        smart_select: bool,
        progress_callback: Callable[[int, int, str], None],
        cancel_check: Callable[[], bool],
        selected_dataset_rank: int = 0,
        log_callback: Optional[Callable[[str], None]] = None
    ) -> Dict[str, Any]:
        """
        Run the complete workflow.

        Args:
            lat: Center latitude
            lon: Center longitude
            size_sqmi: Area size in square miles
            smart_select: Enable smart dataset selection with fallback
            progress_callback: Progress update function (step, total_steps, message)
            cancel_check: Function to check if user cancelled

        Returns:
            Dictionary with keys:
            - hillshade: Path to hillshade file
            - dem: Path to DEM file
            - laz: Path to LAZ file
            - bbox: AOI bounding box
            - metadata: Dataset metadata

        Raises:
            RuntimeError if any step fails
        """
        total_steps = 4

        # Logger function
        def log(msg: str):
            self.log_messages.append(msg)
            print(msg)
            if log_callback:
                log_callback(msg)

        try:
            # Step 1: Compute bbox and find dataset
            progress_callback(1, total_steps, "Finding LiDAR datasets...")
            if cancel_check():
                raise RuntimeError("Cancelled by user")

            bbox = self._compute_bbox(lat, lon, size_sqmi)
            log(f"AOI bbox: {bbox}")

            dataset_rank = selected_dataset_rank
            laz_path = None
            dem_path = None
            ds_props = None
            epsg = None
            point_spacing = 0.0
            dem_resolution = 0.0

            # Try datasets with smart selection fallback
            available_datasets = list_intersecting_datasets(bbox4326=bbox, log=log)
            if not available_datasets:
                raise RuntimeError(
                    "No LiDAR datasets found for this location. "
                    "Try a different area."
                )
            if dataset_rank >= len(available_datasets):
                raise RuntimeError(
                    f"Requested dataset rank {dataset_rank} but only "
                    f"{len(available_datasets)} dataset(s) available."
                )
            max_attempts = (
                len(available_datasets) - dataset_rank if smart_select else 1
            )

            for attempt in range(max_attempts):
                if cancel_check():
                    raise RuntimeError("Cancelled by user")

                log(f"\nAttempt {attempt + 1}: Trying dataset rank {dataset_rank}...")

                # Step 2: Download and clip LiDAR
                progress_callback(2, total_steps, f"Downloading LiDAR data (attempt {attempt + 1})...")

                try:
                    candidate = available_datasets[dataset_rank]
                    source_type = candidate.get("props", {}).get("source_type")
                    if source_type in {"source_dem", "opentopography_dem"}:
                        dem_path, ds_props, epsg, dem_resolution = download_source_dem_for_aoi(
                            dataset=candidate,
                            bbox4326=bbox,
                            center_lat=lat,
                            center_lon=lon,
                            square_miles=size_sqmi,
                            log=log,
                            cancel_check=cancel_check,
                        )
                        log(f"Successfully obtained source DEM: {dem_path.name}")
                    elif source_type == "lpc":
                        laz_path, ds_props, epsg = download_lpc_for_aoi(
                            dataset=candidate,
                            bbox4326=bbox,
                            center_lat=lat,
                            center_lon=lon,
                            square_miles=size_sqmi,
                            log=log,
                            cancel_check=cancel_check,
                        )
                    else:
                        laz_path, ds_props, epsg = clip_aws_aoi_to_ground_laz(
                            bbox4326=bbox,
                            center_lat=lat,
                            center_lon=lon,
                            square_miles=size_sqmi,
                            dataset_rank=dataset_rank,
                            dataset=candidate,
                            log=log,
                            cancel_check=cancel_check,
                        )

                    # Validate point density for point-cloud delivery paths.
                    if laz_path is not None and smart_select:
                        log("Validating point density...")
                        if not validate_laz_point_density(laz_path, size_sqmi, log=log):
                            log("Point density insufficient, trying next dataset...")
                            laz_path = None
                            dataset_rank += 1
                            continue

                    # Success!
                    if laz_path is not None:
                        log(f"Successfully obtained LiDAR data: {laz_path.name}")
                    break

                except RuntimeError as e:
                    error_msg = str(e)
                    if "No LiDAR datasets" in error_msg:
                        # No datasets available at all
                        raise
                    elif "Requested dataset rank" in error_msg and "but only" in error_msg:
                        # Ran out of datasets
                        if smart_select:
                            raise RuntimeError(
                                "All available datasets have insufficient LiDAR coverage for this area."
                            )
                        else:
                            raise
                    elif smart_select and attempt < max_attempts - 1:
                        # Try next dataset
                        log(f"Error with dataset {dataset_rank}: {e}")
                        dataset_rank += 1
                        continue
                    else:
                        # Final attempt failed or smart_select disabled
                        raise

            if laz_path is None and dem_path is None:
                raise RuntimeError("Failed to obtain LiDAR data")

            if cancel_check():
                raise RuntimeError("Cancelled by user")

            # Step 3: Generate a DEM from point clouds. Official source DEMs
            # are already bare-earth rasters at their delivered resolution.
            if dem_path is None:
                progress_callback(3, total_steps, "Creating terrain model...")
                log("\n=== DEM Generation ===")

                dem_path, point_spacing, dem_resolution = run_tin_dem(
                    lidar_path=laz_path,
                    aoi_bbox4326=bbox,
                    epsg_code=epsg,
                    log=log,
                    cancel_check=cancel_check
                )

                log(f"DEM created: {dem_path}")
            else:
                progress_callback(3, total_steps, "Preparing source terrain model...")
                log("Using the official original-resolution bare-earth DEM directly.")

            if cancel_check():
                raise RuntimeError("Cancelled by user")

            # Step 4: Generate hillshade
            progress_callback(4, total_steps, "Generating hillshade...")
            log("\n=== Hillshade Generation ===")

            hillshade_path = generate_classic_hillshade(
                dem_path=dem_path,
                log=log,
                cancel_check=cancel_check
            )

            log(f"Hillshade created: {hillshade_path}")

            # Complete
            progress_callback(4, total_steps, "Complete!")

            return {
                "hillshade": str(hillshade_path),
                "dem": str(dem_path),
                "laz": str(laz_path) if laz_path is not None else None,
                "bbox": bbox,
                "metadata": {
                    "center_lat": lat,
                    "center_lon": lon,
                    "size_sqmi": size_sqmi,
                    "dataset_id": ds_props.get("id"),
                    "workunit": ds_props.get("workunit") or ds_props.get("id"),
                    "collection_year": ds_props.get("collection_year"),
                    "collection_start": ds_props.get("collection_start"),
                    "collection_end": ds_props.get("collection_end"),
                    "quality_level": ds_props.get("quality_level"),
                    "dem_gsd_meters": ds_props.get("dem_gsd_meters"),
                    "lpc_publication_date": ds_props.get("lpc_publication_date"),
                    "horizontal_crs": ds_props.get("horizontal_crs"),
                    "vertical_crs": ds_props.get("vertical_crs"),
                    "geoid": ds_props.get("geoid"),
                    "metadata_source": ds_props.get("metadata_source"),
                    "metadata_link": ds_props.get("metadata_link"),
                    "dataset_citation": ds_props.get("dataset_citation"),
                    "dataset_license": ds_props.get("dataset_license"),
                    "data_source": ds_props.get("source_type"),
                    "source_dem_publication_date": ds_props.get("source_dem_publication_date"),
                    "validation_status": (
                        ds_props.get("source_dem_category")
                        if ds_props.get("source_type") in {"source_dem", "opentopography_dem"}
                        else ds_props.get("lpc_category")
                    ),
                    "epsg": epsg,
                    "point_spacing": point_spacing,
                    "dem_resolution": dem_resolution,
                }
            }

        except Exception as e:
            log(f"ERROR: {e}")
            raise

    def _compute_bbox(
        self,
        lat: float,
        lon: float,
        square_miles: float
    ) -> tuple[float, float, float, float]:
        """
        Compute EPSG:4326 bounding box for square AOI.

        Accounts for latitude-dependent longitude spacing.

        Args:
            lat: Center latitude
            lon: Center longitude
            square_miles: Area size in square miles

        Returns:
            (lon_min, lat_min, lon_max, lat_max)
        """
        # Square side length in miles
        side_miles = math.sqrt(square_miles)
        half_side = side_miles / 2.0

        # Latitude: approximately 69.172 miles per degree
        dlat = half_side / 69.172

        # Longitude: varies with latitude
        cos_lat = math.cos(math.radians(lat))
        if abs(cos_lat) > 0.0001:
            dlon = half_side / (69.172 * cos_lat)
        else:
            dlon = 0.001  # Near poles

        return (
            lon - dlon,  # lon_min
            lat - dlat,  # lat_min
            lon + dlon,  # lon_max
            lat + dlat   # lat_max
        )
