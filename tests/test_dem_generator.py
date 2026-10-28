import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from lidar_core.dem_generator import _fill_dem_nodata  # noqa: E402


class SelectiveDemFillTests(unittest.TestCase):
    def test_fills_small_hole_without_feathering_large_building_void(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            dem_path = Path(temp_dir) / "dem.tif"
            nodata = -9999.0
            data = np.full((100, 100), 12.0, dtype=np.float32)
            data[12:16, 12:16] = nodata
            data[35:75, 35:75] = nodata

            with rasterio.open(
                dem_path,
                "w",
                driver="GTiff",
                width=data.shape[1],
                height=data.shape[0],
                count=1,
                dtype="float32",
                transform=from_origin(0, 100, 1, 1),
                nodata=nodata,
            ) as dem:
                dem.write(data, 1)

            _fill_dem_nodata(
                dem_path,
                max_search_dist=5,
                smoothing=0,
            )

            with rasterio.open(dem_path) as dem:
                result = dem.read(1)
                mask = dem.read_masks(1)

            self.assertTrue(np.all(result[12:16, 12:16] == 12.0))
            self.assertTrue(np.all(mask[12:16, 12:16] == 255))
            self.assertTrue(np.all(result[35:75, 35:75] == nodata))
            self.assertTrue(np.all(mask[35:75, 35:75] == 0))
            np.testing.assert_array_equal(result[:10, :10], data[:10, :10])


if __name__ == "__main__":
    unittest.main()
