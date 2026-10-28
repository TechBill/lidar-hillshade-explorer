import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from utils.cleanup import cleanup_output_folder  # noqa: E402
from utils.config import get_output_dir  # noqa: E402


class SessionOutputCleanupTests(unittest.TestCase):
    def test_output_is_scoped_to_one_process_session(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "utils.config.get_config_dir", return_value=Path(temp_dir)
        ):
            output_dir = get_output_dir()

            self.assertEqual(output_dir.parent, Path(temp_dir) / "output")
            self.assertTrue(output_dir.name.startswith("session-"))
            self.assertTrue((output_dir / "dem").is_dir())
            self.assertTrue((output_dir / "hillshades").is_dir())

    def test_cleanup_preserves_another_app_sessions_files(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "utils.config.get_config_dir", return_value=Path(temp_dir)
        ):
            current_output = get_output_dir()
            current_dem = current_output / "dem" / "current.tif"
            current_dem.write_bytes(b"current")

            other_output = current_output.parent / "session-other-app"
            other_dem = other_output / "dem" / "still-in-use.tif"
            other_dem.parent.mkdir(parents=True)
            other_dem.write_bytes(b"other")

            cleanup_output_folder()

            self.assertFalse(current_output.exists())
            self.assertEqual(other_dem.read_bytes(), b"other")


if __name__ == "__main__":
    unittest.main()
