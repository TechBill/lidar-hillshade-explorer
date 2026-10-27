import unittest
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from lidar_core import aws_operations
from lidar_core.catalogs import ept, opentopography, usgs
from lidar_core.delivery import point_cloud, raster_dem
from lidar_core import ranking


class CompatibilityFacadeTests(unittest.TestCase):
    def test_catalog_api_is_reexported_from_legacy_module(self):
        self.assertIs(aws_operations.list_intersecting_datasets, usgs.list_intersecting_datasets)
        self.assertIs(aws_operations.ensure_aws_index, ept.ensure_aws_index)
        self.assertIs(
            aws_operations._opentopography_candidates,
            opentopography._opentopography_candidates,
        )
        self.assertIs(aws_operations._dataset_sort_key, ranking._dataset_sort_key)

    def test_delivery_api_is_reexported_from_legacy_module(self):
        self.assertIs(
            aws_operations.clip_aws_aoi_to_ground_laz,
            point_cloud.clip_aws_aoi_to_ground_laz,
        )
        self.assertIs(
            aws_operations.download_source_dem_for_aoi,
            raster_dem.download_source_dem_for_aoi,
        )


if __name__ == "__main__":
    unittest.main()
