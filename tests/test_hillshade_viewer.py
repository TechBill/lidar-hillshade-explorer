import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
import tkinter as tk
from unittest.mock import patch

import numpy as np
import rasterio
from rasterio.transform import from_origin


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from hillshade_viewer import HillshadeViewer, viewer_window_size  # noqa: E402


class _VariableStub:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class _EntryStub:
    def __init__(self):
        self.state = None

    def config(self, *, state):
        self.state = state


class _EditableEntryStub:
    def __init__(self):
        self.selection = None
        self.cursor = None

    def after_idle(self, callback, *args):
        callback(*args)

    def selection_range(self, start, end):
        self.selection = (start, end)

    def icursor(self, position):
        self.cursor = position


class CustomModeControlTests(unittest.TestCase):
    def setUp(self):
        self.viewer = HillshadeViewer.__new__(HillshadeViewer)
        self.viewer.multi_var = _VariableStub(False)
        self.viewer.combined_var = _VariableStub(False)
        self.viewer.az_entry = _EntryStub()

    def test_selecting_multidirectional_clears_combined(self):
        self.viewer.multi_var.set(True)
        self.viewer.combined_var.set(True)

        self.viewer._on_multi_changed()

        self.assertFalse(self.viewer.combined_var.get())
        self.assertEqual(self.viewer.az_entry.state, "disabled")

    def test_selecting_combined_clears_multidirectional(self):
        self.viewer.multi_var.set(True)
        self.viewer.combined_var.set(True)

        self.viewer._on_combined_changed()

        self.assertFalse(self.viewer.multi_var.get())
        self.assertEqual(self.viewer.az_entry.state, "normal")

    def test_focusing_custom_input_selects_existing_value(self):
        entry = _EditableEntryStub()

        self.viewer._select_custom_entry_value(SimpleNamespace(widget=entry))

        self.assertEqual(entry.selection, (0, tk.END))
        self.assertEqual(entry.cursor, tk.END)


class ViewerWindowSizeTests(unittest.TestCase):
    def test_large_display_opens_tall_enough_for_sidebar(self):
        self.assertEqual(viewer_window_size(1920, 1080, 890), (1200, 902))

    def test_short_display_keeps_viewer_inside_available_height(self):
        self.assertEqual(viewer_window_size(1366, 768, 890), (1200, 668))

    def test_existing_baseline_size_is_preserved_for_short_content(self):
        self.assertEqual(viewer_window_size(1920, 1080, 650), (1200, 800))


class ViewerSidebarScrollingTests(unittest.TestCase):
    def setUp(self):
        self.viewer = HillshadeViewer.__new__(HillshadeViewer)
        self.viewer.sidebar_frame = SimpleNamespace(master=None)
        self.scroll_calls = []
        self.viewer.sidebar_canvas = SimpleNamespace(
            yview=lambda: (0.0, 0.7),
            yview_scroll=lambda *args: self.scroll_calls.append(args),
        )

    def event(self, *, delta=0, num=None, in_sidebar=True):
        widget = SimpleNamespace(
            master=self.viewer.sidebar_frame if in_sidebar else None
        )
        return SimpleNamespace(widget=widget, delta=delta, num=num)

    def test_mouse_wheel_scrolls_over_nested_sidebar_controls(self):
        self.assertEqual(
            self.viewer._on_sidebar_mousewheel(self.event(delta=-120)), "break"
        )
        self.assertEqual(
            self.viewer._on_sidebar_mousewheel(self.event(delta=120)), "break"
        )
        self.assertEqual(self.scroll_calls, [(1, "units"), (-1, "units")])

    def test_linux_wheel_buttons_scroll_sidebar(self):
        self.viewer._on_sidebar_mousewheel(self.event(num=4))
        self.viewer._on_sidebar_mousewheel(self.event(num=5))
        self.assertEqual(self.scroll_calls, [(-1, "units"), (1, "units")])

    def test_does_not_intercept_image_canvas_wheel(self):
        result = self.viewer._on_sidebar_mousewheel(
            self.event(delta=-120, in_sidebar=False)
        )
        self.assertIsNone(result)
        self.assertEqual(self.scroll_calls, [])

    def test_does_not_consume_wheel_when_sidebar_does_not_overflow(self):
        self.viewer.sidebar_canvas.yview = lambda: (0.0, 1.0)
        result = self.viewer._on_sidebar_mousewheel(self.event(delta=-120))
        self.assertIsNone(result)
        self.assertEqual(self.scroll_calls, [])


class ViewerNoDataRenderingTests(unittest.TestCase):
    def test_opaque_preference_matches_kmz_fill_without_changing_geotiff(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "sample_hillshade.tif"
            data = np.full((5, 5), 180, dtype=np.uint8)
            data[2, 2] = 0
            with rasterio.open(
                path,
                "w",
                driver="GTiff",
                width=5,
                height=5,
                count=1,
                dtype="uint8",
                crs="EPSG:4326",
                transform=from_origin(-90.0, 38.0, 0.00001, 0.00001),
                nodata=0,
            ) as destination:
                destination.write(data, 1)

            viewer = HillshadeViewer.__new__(HillshadeViewer)
            viewer._reset_view_to_fit = lambda: None
            viewer.redraw = lambda: None
            with patch(
                "hillshade_viewer.load_config",
                return_value={"preferences": {"opaque_kmz_nodata": True}},
            ):
                viewer._load_and_display_hillshade(path)

            self.assertEqual(viewer._pil_image.mode, "RGB")
            self.assertEqual(viewer._pil_image.getpixel((2, 2)), (180, 180, 180))
            with rasterio.open(path) as source:
                self.assertEqual(source.nodata, 0)
                self.assertEqual(source.read(1)[2, 2], 0)
                self.assertTrue(source.read(1, masked=True).mask[2, 2])


if __name__ == "__main__":
    unittest.main()
