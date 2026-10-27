import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from lidar_core.hillshade_engine import (  # noqa: E402
    TERRAIN_PRESETS,
    custom_hillshade_path,
    generate_custom_hillshade,
    generate_preset_hillshade,
)


class CustomHillshadeTests(unittest.TestCase):
    def test_combined_detail_matches_hires_preset(self):
        preset = TERRAIN_PRESETS["Combined Detail"]

        self.assertEqual(preset["z"], 2.0)
        self.assertEqual(preset["alt"], 25.0)
        self.assertEqual(preset["az"], 315.0)
        self.assertFalse(preset["multi"])
        self.assertTrue(preset["combined"])

    def test_modes_have_distinct_output_paths(self):
        dem_path = Path("sample_dem.tif")

        with patch(
            "lidar_core.hillshade_engine.get_output_dir",
            return_value=Path("output"),
        ):
            directional = custom_hillshade_path(
                dem_path, 1.0, 45.0, 315.0, False, False
            )
            multidirectional = custom_hillshade_path(
                dem_path, 1.0, 45.0, 315.0, True, False
            )
            combined = custom_hillshade_path(
                dem_path, 1.0, 45.0, 315.0, False, True
            )

        self.assertEqual(len({directional, multidirectional, combined}), 3)
        self.assertIn("_multi", multidirectional.name)
        self.assertIn("_combined", combined.name)

    def test_rejects_incompatible_modes(self):
        with self.assertRaisesRegex(ValueError, "cannot be used at the same time"):
            custom_hillshade_path(
                Path("sample_dem.tif"), 1.0, 45.0, 315.0, True, True
            )

    def test_combined_uses_gdal_combined_flag(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            captured = {}

            def capture_command(cmd, output_path, cancel_check=None, log=None):
                captured["cmd"] = cmd
                output_path.touch()

            with (
                patch(
                    "lidar_core.hillshade_engine.get_output_dir",
                    return_value=output_dir,
                ),
                patch(
                    "lidar_core.hillshade_engine.find_gdaldem",
                    return_value="gdaldem",
                ),
                patch("lidar_core.hillshade_engine.load_config", return_value={}),
                patch(
                    "lidar_core.hillshade_engine._run_gdaldem",
                    side_effect=capture_command,
                ),
            ):
                generate_custom_hillshade(
                    Path("sample_dem.tif"),
                    1.0,
                    45.0,
                    315.0,
                    multidirectional=False,
                    combined=True,
                )

        self.assertIn("-combined", captured["cmd"])
        self.assertNotIn("-multidirectional", captured["cmd"])
        self.assertEqual(captured["cmd"][captured["cmd"].index("-az") + 1], "315.0")
        self.assertIn("-compute_edges", captured["cmd"])

    def test_combined_detail_preset_uses_gdal_combined_flag(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            captured = {}

            def capture_command(cmd, output_path, cancel_check=None, log=None):
                captured["cmd"] = cmd
                captured["output_path"] = output_path
                output_path.touch()

            with (
                patch(
                    "lidar_core.hillshade_engine.get_output_dir",
                    return_value=output_dir,
                ),
                patch(
                    "lidar_core.hillshade_engine.find_gdaldem",
                    return_value="gdaldem",
                ),
                patch("lidar_core.hillshade_engine.load_config", return_value={}),
                patch(
                    "lidar_core.hillshade_engine._run_gdaldem",
                    side_effect=capture_command,
                ),
            ):
                result = generate_preset_hillshade(
                    Path("sample_dem.tif"),
                    "Combined Detail",
                )

        self.assertEqual(result, captured["output_path"])
        self.assertIn("_preset_Combined_Detail", result.name)
        self.assertIn("-combined", captured["cmd"])
        self.assertNotIn("-multidirectional", captured["cmd"])
        self.assertEqual(captured["cmd"][captured["cmd"].index("-z") + 1], "2.0")
        self.assertEqual(captured["cmd"][captured["cmd"].index("-alt") + 1], "25.0")
        self.assertEqual(captured["cmd"][captured["cmd"].index("-az") + 1], "315.0")


if __name__ == "__main__":
    unittest.main()
