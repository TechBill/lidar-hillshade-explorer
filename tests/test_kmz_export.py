import tempfile
import unittest
import zipfile
import xml.etree.ElementTree as ET
import sys
from io import BytesIO
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from rasterio.transform import from_origin


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from lidar_core.kmz_export import (
    KMZ_MAX_TILES,
    KMZ_TILE_SIZE,
    _tile_grid_dimensions,
    tifs_to_kmz,
)


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

    def test_large_hillshade_uses_flat_web_compatible_tiles(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            tif_path = temp / "sample_preset_Combined_Detail.tif"
            kmz_path = temp / "sample.kmz"
            width, height = 4100, 2100
            data = np.full((height, width), 180, dtype=np.uint8)
            with rasterio.open(
                tif_path,
                "w",
                driver="GTiff",
                width=width,
                height=height,
                count=1,
                dtype="uint8",
                crs="EPSG:4326",
                transform=from_origin(-90.0, 38.0, 0.00001, 0.00001),
                nodata=0,
            ) as dst:
                dst.write(data, 1)

            tifs_to_kmz([tif_path], kmz_path, opaque_nodata=True)

            with zipfile.ZipFile(kmz_path) as archive:
                kml_bytes = archive.read("doc.kml")
                kml_text = kml_bytes.decode("utf-8")
                self.assertNotIn("NetworkLink", kml_text)
                self.assertNotIn("<Region", kml_text)

                root = ET.fromstring(kml_bytes)
                namespace = {"kml": "http://www.opengis.net/kml/2.2"}
                overlays = root.findall(".//kml:GroundOverlay", namespace)
                self.assertEqual(len(overlays), 6)

                hrefs = [
                    overlay.findtext("kml:Icon/kml:href", namespaces=namespace)
                    for overlay in overlays
                ]
                self.assertEqual(len(set(hrefs)), 6)
                self.assertTrue(all(name.startswith("files/tile_") for name in hrefs))

                for href in hrefs:
                    self.assertIn(href, archive.namelist())
                    with Image.open(BytesIO(archive.read(href))) as image:
                        self.assertLessEqual(image.width, KMZ_TILE_SIZE)
                        self.assertLessEqual(image.height, KMZ_TILE_SIZE)

                north = max(
                    float(
                        overlay.findtext(
                            "kml:LatLonBox/kml:north", namespaces=namespace
                        )
                    )
                    for overlay in overlays
                )
                south = min(
                    float(
                        overlay.findtext(
                            "kml:LatLonBox/kml:south", namespaces=namespace
                        )
                    )
                    for overlay in overlays
                )
                east = max(
                    float(
                        overlay.findtext(
                            "kml:LatLonBox/kml:east", namespaces=namespace
                        )
                    )
                    for overlay in overlays
                )
                west = min(
                    float(
                        overlay.findtext(
                            "kml:LatLonBox/kml:west", namespaces=namespace
                        )
                    )
                    for overlay in overlays
                )
                self.assertAlmostEqual(north, 38.0)
                self.assertAlmostEqual(south, 38.0 - height * 0.00001)
                self.assertAlmostEqual(west, -90.0)
                self.assertAlmostEqual(east, -90.0 + width * 0.00001)

    def test_web_compatibility_limits_are_enforced(self):
        columns, rows, tile_count = _tile_grid_dimensions(9000, 9000)
        self.assertEqual((columns, rows, tile_count), (5, 5, 25))
        self.assertGreater(tile_count, KMZ_MAX_TILES)
        with self.assertRaisesRegex(ValueError, "exactly one hillshade"):
            tifs_to_kmz(
                [Path("first.tif"), Path("second.tif")],
                Path("unused.kmz"),
            )


if __name__ == "__main__":
    unittest.main()
