import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from lidar_core.aws_operations import (  # noqa: E402
    MIN_WESM_RECORDS,
    _dataset_sort_key,
    _enrich_dataset_properties,
    _parse_wesm_csv,
    _validate_wesm_records,
)
from utils.config import (  # noqa: E402
    TERRAIN_STYLE_CONTINUOUS,
    TERRAIN_STYLE_CUSTOM,
    TERRAIN_STYLE_PRESERVE,
    get_effective_dem_settings,
)


WESM_SAMPLE = """workunit,collect_start,collect_end,ql,dem_gsd_meters,horiz_crs,vert_crs,geoid,lpc_pub_date,metadata_link
MO_SouthernMO_1_D22,2023/03/15,2023/03/20,QL 2,1,NAD83(2011) / UTM zone 15N,NAVD88,GEOID18,2023/12/20,https://example.test/d22
MO_SE11County_1_B24,2024/03/17,2024/04/13,QL 1,0.5,NAD83(2011) / UTM zone 15N,NAVD88,GEOID18,2025/07/18,https://example.test/b24
MO_FEMAR7_North_A1_2017,2017/01/01,2017/12/31,QL 2,1,NAD83 / UTM zone 15N,NAVD88,GEOID12B,2019/01/01,https://example.test/2017
"""


class TerrainStyleTests(unittest.TestCase):
    def test_continuous_profile_overrides_custom_values(self):
        config = {
            "preferences": {"terrain_style": TERRAIN_STYLE_CONTINUOUS},
            "dem_fill": {
                "tin_max_edge_multiplier": 12,
                "fill_max_search": 16,
                "fill_smoothing": 4,
            },
        }
        settings = get_effective_dem_settings(config)
        self.assertEqual(settings["tin_max_edge_multiplier"], 40)
        self.assertEqual(settings["fill_max_search"], 64)
        self.assertEqual(settings["fill_smoothing"], 2)

    def test_preserve_and_custom_profiles(self):
        preserve = get_effective_dem_settings(
            {"preferences": {"terrain_style": TERRAIN_STYLE_PRESERVE}}
        )
        self.assertEqual(preserve["tin_max_edge_multiplier"], 12)
        self.assertEqual(preserve["fill_max_search"], 16)

        custom = get_effective_dem_settings(
            {
                "preferences": {"terrain_style": TERRAIN_STYLE_CUSTOM},
                "dem_fill": {"tin_max_edge_multiplier": 25, "fill_max_search": 31},
            }
        )
        self.assertEqual(custom["tin_max_edge_multiplier"], 25)
        self.assertEqual(custom["fill_max_search"], 31)


class WesmSelectionTests(unittest.TestCase):
    def setUp(self):
        self.records = _parse_wesm_csv(WESM_SAMPLE)

    def test_authoritative_collection_date_beats_name_guess(self):
        props = _enrich_dataset_properties(
            "MO_SouthernMO_1_D22", {"name": "MO_SouthernMO_1_D22"}, self.records
        )
        self.assertEqual(props["collection_year"], 2023)
        self.assertEqual(props["quality_level"], 2)
        self.assertEqual(props["workunit"], "MO_SouthernMO_1_D22")
        self.assertEqual(props["horizontal_crs"], "NAD83(2011) / UTM zone 15N")
        self.assertEqual(props["vertical_crs"], "NAVD88")
        self.assertEqual(props["geoid"], "GEOID18")
        self.assertEqual(props["lpc_publication_date"], "2023-12-20")
        self.assertFalse(props["year_estimated"])

    def test_legacy_ept_alias_matches_workunit(self):
        props = _enrich_dataset_properties(
            "USGS_LPC_MO_FEMAR7_North_A1_2017_LAS_2019",
            {"name": "USGS_LPC_MO_FEMAR7_North_A1_2017_LAS_2019"},
            self.records,
        )
        self.assertEqual(props["collection_year"], 2017)
        self.assertFalse(props["year_estimated"])

    def test_filename_year_is_marked_estimated_when_unmatched(self):
        props = _enrich_dataset_properties(
            "Example_2021", {"name": "Example_2021"}, self.records
        )
        self.assertEqual(props["collection_year"], 2021)
        self.assertTrue(props["year_estimated"])

    def test_sort_uses_collection_end_then_quality(self):
        older = _enrich_dataset_properties(
            "MO_SouthernMO_1_D22", {"name": "MO_SouthernMO_1_D22"}, self.records
        )
        newer = _enrich_dataset_properties(
            "MO_SE11County_1_B24", {"name": "MO_SE11County_1_B24"}, self.records
        )
        self.assertGreater(
            _dataset_sort_key("MO_SE11County_1_B24", newer),
            _dataset_sort_key("MO_SouthernMO_1_D22", older),
        )

    def test_validation_rejects_truncated_nationwide_export(self):
        with self.assertRaisesRegex(ValueError, "contained only 3 work units"):
            _validate_wesm_records(self.records)

    def test_validation_accepts_complete_export_shape(self):
        template = next(iter(self.records.values()))
        records = {
            f"workunit{index}": {**template, "workunit": f"WorkUnit_{index}"}
            for index in range(MIN_WESM_RECORDS)
        }
        _validate_wesm_records(records)


if __name__ == "__main__":
    unittest.main()
