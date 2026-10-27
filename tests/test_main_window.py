import unittest
from types import SimpleNamespace

from app import main_window_size
from main_gui import HillshadeExplorerApp


class MainWindowSizeTests(unittest.TestCase):
    def test_uses_roomier_default_on_large_display(self):
        self.assertEqual(main_window_size(1920, 1080), (520, 840))

    def test_keeps_window_inside_short_laptop_display(self):
        self.assertEqual(main_window_size(1366, 768), (520, 668))

    def test_keeps_a_usable_floor_on_very_small_reported_display(self):
        self.assertEqual(main_window_size(400, 500), (360, 400))


class MainWindowScrollingTests(unittest.TestCase):
    def setUp(self):
        self.app = HillshadeExplorerApp.__new__(HillshadeExplorerApp)
        self.app.root = object()
        self.scroll_calls = []
        self.app.main_canvas = SimpleNamespace(
            yview_scroll=lambda *args: self.scroll_calls.append(args)
        )

    def event(self, *, delta=0, num=None, root=None):
        event_root = self.app.root if root is None else root
        widget = SimpleNamespace(winfo_toplevel=lambda: event_root)
        return SimpleNamespace(widget=widget, delta=delta, num=num)

    def test_mouse_wheel_scrolls_in_both_directions(self):
        self.assertEqual(self.app._on_main_mousewheel(self.event(delta=-120)), "break")
        self.assertEqual(self.app._on_main_mousewheel(self.event(delta=120)), "break")
        self.assertEqual(self.scroll_calls, [(1, "units"), (-1, "units")])

    def test_linux_wheel_buttons_scroll(self):
        self.app._on_main_mousewheel(self.event(num=4))
        self.app._on_main_mousewheel(self.event(num=5))
        self.assertEqual(self.scroll_calls, [(-1, "units"), (1, "units")])

    def test_ignores_wheel_events_from_another_window(self):
        self.assertIsNone(
            self.app._on_main_mousewheel(self.event(delta=-120, root=object()))
        )
        self.assertEqual(self.scroll_calls, [])


if __name__ == "__main__":
    unittest.main()
