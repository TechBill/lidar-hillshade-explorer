"""Opt-in real-network catalog checks.

Run explicitly with:
LHE_RUN_NETWORK_TESTS=1 python3 -m unittest discover -s tests/integration -v
"""

import os
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from lidar_core.catalogs.opentopography import _fetch_json_url  # noqa: E402
from lidar_core.catalogs.usiei import _query_usiei_topographic  # noqa: E402


@unittest.skipUnless(
    os.environ.get("LHE_RUN_NETWORK_TESTS") == "1",
    "set LHE_RUN_NETWORK_TESTS=1 to run live catalog tests",
)
class LiveCatalogTests(unittest.TestCase):
    def test_usiei_returns_southeast_missouri_project(self):
        records = _query_usiei_topographic(
            (-90.46, 37.09, -90.44, 37.11), use_cache=False
        )
        titles = " ".join(
            str(item.get("properties", {}).get("Title") or "") for item in records
        )
        self.assertIn("Missouri Southeast", titles)

    def test_opentopography_public_catalog_is_json(self):
        payload = _fetch_json_url(
            "https://portal.opentopography.org/API/otCatalog?"
            "productFormat=Raster&minx=-93.01&miny=36.99&maxx=-92.99&maxy=37.01&"
            "detail=false&outputFormat=json&include_federated=false"
        )
        self.assertIsInstance(payload.get("Datasets", []), list)


if __name__ == "__main__":
    unittest.main()
