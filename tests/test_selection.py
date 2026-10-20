import sys
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from lidar_core.aws_operations import (  # noqa: E402
    MIN_WESM_RECORDS,
    _dataset_sort_key,
    _enrich_dataset_properties,
    _geometry_intersects_bbox,
    _parse_wesm_csv,
    _product_workunit_key,
    _products_cover_bbox,
    _validate_wesm_records,
    list_intersecting_datasets,
)
from processing import ProcessingOrchestrator  # noqa: E402
from utils.config import (  # noqa: E402
    TERRAIN_STYLE_CONTINUOUS,
    TERRAIN_STYLE_CUSTOM,
    TERRAIN_STYLE_PRESERVE,
    get_effective_dem_settings,
)


WESM_SAMPLE = """workunit,collect_start,collect_end,ql,dem_gsd_meters,horiz_crs,vert_crs,geoid,lpc_pub_date,lpc_category,sourcedem_pub_date,sourcedem_category,lpc_link,sourcedem_link,metadata_link
MO_SouthernMO_1_D22,2023/03/15,2023/03/20,QL 2,1,NAD83(2011) / UTM zone 15N,NAVD88,GEOID18,2023/12/20,Meets,2023/12/20,Meets,https://example.test/lpc/d22,https://example.test/dem/d22,https://example.test/d22
MO_SE11County_1_B24,2024/03/17,2024/04/13,QL 1,0.5,NAD83(2011) / UTM zone 15N,NAVD88,GEOID18,2025/07/18,Meets,2025/07/18,Meets,https://example.test/lpc/b24,https://example.test/dem/b24,https://example.test/b24
MO_FEMAR7_North_A1_2017,2017/01/01,2017/12/31,QL 2,1,NAD83 / UTM zone 15N,NAVD88,GEOID12B,2019/01/01,Meets,2019/01/01,Meets,https://example.test/lpc/2017,https://example.test/dem/2017,https://example.test/2017
"""


def polygon_feature(name, coordinates):
    """Build a minimal coverage-index feature for selection tests."""
    return {
        "type": "Feature",
        "properties": {"name": name},
        "geometry": {"type": "MultiPolygon", "coordinates": coordinates},
    }


class CoverageGeometryTests(unittest.TestCase):
    def test_rejects_gap_inside_dataset_envelope(self):
        # Two disjoint coverage islands have an envelope spanning the AOI, but
        # neither real polygon touches it.
        geometry = {
            "type": "MultiPolygon",
            "coordinates": [
                [[[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]]],
                [[[8, 0], [10, 0], [10, 2], [8, 2], [8, 0]]],
            ],
        }
        self.assertFalse(_geometry_intersects_bbox(geometry, (4, 0.5, 6, 1.5)))

    def test_detects_bbox_inside_polygon_and_boundary_crossing(self):
        geometry = {
            "type": "Polygon",
            "coordinates": [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]],
        }
        self.assertTrue(_geometry_intersects_bbox(geometry, (4, 4, 6, 6)))
        self.assertTrue(_geometry_intersects_bbox(geometry, (9, 4, 11, 6)))

    def test_rejects_bbox_fully_inside_polygon_hole(self):
        geometry = {
            "type": "Polygon",
            "coordinates": [
                [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]],
                [[3, 3], [7, 3], [7, 7], [3, 7], [3, 3]],
            ],
        }
        self.assertFalse(_geometry_intersects_bbox(geometry, (4, 4, 6, 6)))

    @patch("lidar_core.aws_operations.ensure_wesm_metadata", return_value={})
    @patch("lidar_core.aws_operations._query_tnm_products", return_value=[])
    @patch("lidar_core.aws_operations.ensure_aws_index")
    def test_selection_uses_real_coverage_not_envelope(
        self, mock_index, _mock_tnm, _mock_wesm
    ):
        false_positive = polygon_feature(
            "Newer_false_positive_2024",
            [
                [[[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]]],
                [[[8, 0], [10, 0], [10, 2], [8, 2], [8, 0]]],
            ],
        )
        valid = polygon_feature(
            "Older_valid_2017",
            [[[[4, 0], [6, 0], [6, 2], [4, 2], [4, 0]]]],
        )
        mock_index.return_value = {"features": [false_positive, valid]}

        datasets = list_intersecting_datasets((4.5, 0.5, 5.5, 1.5))

        self.assertEqual([dataset["id"] for dataset in datasets], ["Older_valid_2017"])

    def test_product_tiles_must_cover_complete_aoi(self):
        def product(left, bottom, right, top):
            return {
                "boundingBox": {
                    "minX": left,
                    "minY": bottom,
                    "maxX": right,
                    "maxY": top,
                }
            }

        complete = [product(0, 0, 1, 2), product(1, 0, 2, 2)]
        gap = [product(0, 0, 0.9, 2), product(1.1, 0, 2, 2)]
        self.assertTrue(_products_cover_bbox(complete, (0, 0, 2, 2)))
        self.assertFalse(_products_cover_bbox(gap, (0, 0, 2, 2)))


class SmartSelectionTests(unittest.TestCase):
    def test_smart_selection_tries_every_available_dataset(self):
        datasets = [
            {"id": f"dataset_{rank}", "props": {}}
            for rank in range(6)
        ]
        clip_results = [RuntimeError("empty point cloud") for _ in range(5)]
        clip_results.append(
            (Path("valid.laz"), {"id": "dataset_5"}, 3857)
        )

        with (
            patch("processing.list_intersecting_datasets", return_value=datasets),
            patch(
                "processing.clip_aws_aoi_to_ground_laz",
                side_effect=clip_results,
            ) as mock_clip,
            patch("processing.validate_laz_point_density", return_value=True),
            patch(
                "processing.run_tin_dem",
                return_value=(Path("dem.tif"), 1.0, 1.0),
            ),
            patch(
                "processing.generate_classic_hillshade",
                return_value=Path("hillshade.tif"),
            ),
        ):
            result = ProcessingOrchestrator().run_workflow(
                lat=38.4249,
                lon=-92.9362,
                size_sqmi=0.25,
                smart_select=True,
                progress_callback=lambda *_args: None,
                cancel_check=lambda: False,
            )

        self.assertEqual(mock_clip.call_count, 6)
        self.assertEqual(result["metadata"]["dataset_id"], "dataset_5")

    def test_source_dem_skips_point_cloud_dem_generation(self):
        candidate = {
            "id": "source_workunit",
            "props": {"id": "source_workunit", "source_type": "source_dem"},
        }
        with (
            patch("processing.list_intersecting_datasets", return_value=[candidate]),
            patch(
                "processing.download_source_dem_for_aoi",
                return_value=(
                    Path("source_dem.tif"),
                    candidate["props"],
                    6342,
                    0.25,
                ),
            ),
            patch("processing.run_tin_dem") as mock_tin,
            patch(
                "processing.generate_classic_hillshade",
                return_value=Path("hillshade.tif"),
            ),
        ):
            result = ProcessingOrchestrator().run_workflow(
                lat=43.8,
                lon=-103.8,
                size_sqmi=0.25,
                smart_select=True,
                progress_callback=lambda *_args: None,
                cancel_check=lambda: False,
            )

        mock_tin.assert_not_called()
        self.assertIsNone(result["laz"])
        self.assertEqual(result["dem"], "source_dem.tif")
        self.assertEqual(result["metadata"]["dem_resolution"], 0.25)


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

    def test_tnm_product_path_resolves_wesm_workunit(self):
        product = {
            "downloadURL": (
                "https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/OPR/"
                "Projects/MO_Project/MO_SE11County_1_B24/TIFF/tile.tif"
            )
        }
        self.assertEqual(
            _product_workunit_key(product, self.records),
            "mose11county1b24",
        )

    @patch("lidar_core.aws_operations._ept_candidates", return_value={})
    @patch("lidar_core.aws_operations._query_tnm_products")
    @patch("lidar_core.aws_operations.ensure_wesm_metadata")
    def test_wesm_first_discovery_finds_non_ept_source_dem(
        self, mock_wesm, mock_query, _mock_ept
    ):
        mock_wesm.return_value = self.records
        source_product = {
            "downloadURL": (
                "https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/OPR/"
                "Projects/MO_Project/MO_SE11County_1_B24/TIFF/tile.tif"
            ),
            "boundingBox": {"minX": 0, "minY": 0, "maxX": 2, "maxY": 2},
        }
        mock_query.side_effect = [[source_product], []]

        datasets = list_intersecting_datasets((0.5, 0.5, 1.5, 1.5))

        self.assertEqual(len(datasets), 1)
        self.assertEqual(datasets[0]["id"], "MO_SE11County_1_B24")
        self.assertEqual(datasets[0]["props"]["source_type"], "source_dem")
        self.assertTrue(datasets[0]["props"]["covers_aoi"])

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
