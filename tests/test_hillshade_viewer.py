import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
import tkinter as tk


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


if __name__ == "__main__":
    unittest.main()
