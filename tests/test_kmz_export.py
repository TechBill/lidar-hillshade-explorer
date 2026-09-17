import tempfile
import unittest
import zipfile
from io import BytesIO
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from rasterio.transform import from_origin

from lidar_core.kmz_export import tifs_to_kmz


class KmzNoDataExportTests(unittest.TestCase):
    def _write_hillshade(self, path: Path) -> None:
        data = np.full((600, 600), 180, dtype=np.uint8)
        data[270:330, 270:330] = 0
        with rasterio.open(
            path,
            "w",
            driver="GTiff",
            width=600,
            height=600,
            count=1,
            dtype="uint8",
            crs="EPSG:4326",
            transform=from_origin(-90.0, 38.0, 0.00001, 0.00001),
            nodata=0,
        ) as dst:
            dst.write(data, 1)

    def test_standard_kmz_preserves_nodata_transparency_by_default(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            tif_path = temp / "sample_classic.tif"
            kmz_path = temp / "sample.kmz"
            self._write_hillshade(tif_path)

            tifs_to_kmz([tif_path], kmz_path)

            with zipfile.ZipFile(kmz_path) as archive:
                names = archive.namelist()
                self.assertIn("doc.kml", names)
                png_names = [name for name in names if name.endswith(".png")]
                self.assertEqual(len(png_names), 1)
                with Image.open(BytesIO(archive.read(png_names[0]))) as image:
                    self.assertEqual(image.mode, "RGBA")
                    self.assertEqual(image.getpixel((300, 300))[3], 0)

    def test_opaque_nodata_produces_rgb_overlay(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            tif_path = temp / "sample_classic.tif"
            kmz_path = temp / "sample.kmz"
            self._write_hillshade(tif_path)

            tifs_to_kmz(
                [tif_path],
                kmz_path,
                opaque_nodata=True,
            )

            with zipfile.ZipFile(kmz_path) as archive:
                png_name = next(
                    name for name in archive.namelist() if name.endswith(".png")
                )
                with Image.open(BytesIO(archive.read(png_name))) as image:
                    self.assertEqual(image.mode, "RGB")
                    self.assertEqual(image.getpixel((300, 300)), (180, 180, 180))


if __name__ == "__main__":
    unittest.main()
