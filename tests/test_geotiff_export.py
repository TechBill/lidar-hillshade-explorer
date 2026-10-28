import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import ColorInterp
from rasterio.transform import from_origin


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from lidar_core.geotiff_export import export_cog  # noqa: E402
from lidar_core.kmz_export import render_hillshade_image  # noqa: E402


class CloudOptimizedGeoTiffExportTests(unittest.TestCase):
    def test_export_is_lossless_validated_cog(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "source.tif"
            export_path = Path(temp_dir) / "exported.tif"
            height, width = 1200, 1300
            rows, columns = np.indices((height, width), dtype=np.uint32)
            data = ((rows * 17 + columns * 29) % 255 + 1).astype(np.uint8)
            data[100:125, 200:240] = 0
            transform = from_origin(-90.5, 38.5, 0.5, 0.5)

            with rasterio.open(
                source_path,
                "w",
                driver="GTiff",
                width=width,
                height=height,
                count=1,
                dtype="uint8",
                crs="EPSG:26915",
                transform=transform,
                nodata=0,
            ) as source:
                source.write(data, 1)
                source.colorinterp = (ColorInterp.gray,)
                source.set_band_description(1, "Hillshade intensity")
                source.update_tags(SURVEY="COG export test", PURPOSE="unit test")
                source.update_tags(1, BAND_ROLE="hillshade")

            validation = export_cog(source_path, export_path)

            self.assertEqual(validation.layout, "COG")
            self.assertEqual(validation.block_shapes, ((512, 512),))
            self.assertTrue(validation.overview_levels)
            self.assertFalse(export_path.with_suffix(".tif.ovr").exists())

            with rasterio.open(source_path) as source, rasterio.open(
                export_path
            ) as exported:
                self.assertEqual(exported.tags(ns="IMAGE_STRUCTURE")["LAYOUT"], "COG")
                self.assertEqual(
                    exported.tags(ns="IMAGE_STRUCTURE")["COMPRESSION"],
                    "DEFLATE",
                )
                self.assertEqual(exported.tags(ns="IMAGE_STRUCTURE")["PREDICTOR"], "2")
                self.assertEqual(exported.block_shapes, [(512, 512)])
                self.assertTrue(exported.overviews(1))
                self.assertEqual(exported.crs, source.crs)
                self.assertEqual(exported.transform, source.transform)
                self.assertEqual(exported.shape, source.shape)
                self.assertEqual(exported.count, source.count)
                self.assertEqual(exported.dtypes, source.dtypes)
                self.assertEqual(exported.nodatavals, source.nodatavals)
                self.assertEqual(exported.colorinterp, (ColorInterp.gray,))
                self.assertEqual(exported.descriptions, ("Hillshade intensity",))
                self.assertEqual(exported.tags()["SURVEY"], "COG export test")
                self.assertEqual(exported.tags(1)["BAND_ROLE"], "hillshade")
                self.assertEqual(exported.checksum(1), source.checksum(1))
                np.testing.assert_array_equal(exported.read(1), source.read(1))

            with rasterio.open(export_path) as exported:
                rendered = render_hillshade_image(exported.read(1, masked=True))
            self.assertEqual(rendered.mode, "RGBA")
            self.assertEqual(rendered.size, (width, height))


if __name__ == "__main__":
    unittest.main()
