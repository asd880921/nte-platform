import importlib.util
import os
import sys
import unittest
from unittest import mock


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE_PACKAGES = os.path.join(ROOT, ".venv", "Lib", "site-packages")
if os.path.isdir(SITE_PACKAGES):
    sys.path[:0] = [
        SITE_PACKAGES,
        os.path.join(SITE_PACKAGES, "win32"),
        os.path.join(SITE_PACKAGES, "win32", "lib"),
        os.path.join(SITE_PACKAGES, "Pythonwin"),
    ]
    dll_dir = os.path.join(SITE_PACKAGES, "pywin32_system32")
    if hasattr(os, "add_dll_directory") and os.path.isdir(dll_dir):
        os.add_dll_directory(dll_dir)
MODULE_PATH = os.path.join(ROOT, "scripts", "999_nights", "main.py")
SPEC = importlib.util.spec_from_file_location("nights", MODULE_PATH)
NIGHTS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(NIGHTS)


class NavigationTests(unittest.TestCase):
    def setUp(self):
        NIGHTS._STOP_RUN.clear()

    def test_regular_movement_holds_w_for_the_whole_path(self):
        observations = iter(
            [
                ((0.0, 0.0, 0.0), (50.0, 0.0), 1.0),
                ((0.0, 0.0, 0.0), (30.0, 0.0), 1.0),
                ((0.0, 0.0, 0.0), (10.0, 0.0), 1.0),
            ]
        )
        events = []
        keyboard = mock.Mock()
        keyboard.press.side_effect = lambda key: events.append(("down", key))
        keyboard.release.side_effect = lambda key: events.append(("up", key))

        with (
            mock.patch.object(NIGHTS, "observe_target", side_effect=observations),
            mock.patch.object(NIGHTS, "steer_toward"),
            mock.patch.object(NIGHTS, "sleep_check"),
            mock.patch.object(NIGHTS, "keyboard", keyboard),
        ):
            NIGHTS.navigate_to(1, "door")

        w_events = [event for event in events if event[1] == "w"]
        self.assertEqual(w_events, [("down", "w"), ("up", "w")])

    def test_large_heading_error_uses_a_fast_camera_turn(self):
        mouse_event = mock.Mock()
        with mock.patch.object(NIGHTS.win32api, "mouse_event", mouse_event):
            NIGHTS.steer_toward((0.0, 0.0, 0.0), (0.0, 100.0))

        horizontal_pixels = mouse_event.call_args.args[1]
        self.assertGreaterEqual(abs(horizontal_pixels), 300)

    def test_campfire_stops_as_soon_as_interaction_prompt_appears(self):
        observations = iter(
            [
                ((0.0, 0.0, 0.0), (40.0, 0.0), 1.0),
                ((0.0, 0.0, 0.0), (20.0, 0.0), 1.0),
                ((0.0, 0.0, 0.0), (7.0, 0.0), 1.0),
            ]
        )
        observe = mock.Mock(side_effect=observations)
        prompt_visible = mock.Mock(side_effect=(False, True))
        keyboard = mock.Mock()
        with (
            mock.patch.object(NIGHTS, "observe_target", observe),
            mock.patch.object(NIGHTS, "steer_toward"),
            mock.patch.object(NIGHTS, "sleep_check"),
            mock.patch.object(NIGHTS, "keyboard", keyboard),
            mock.patch.object(
                NIGHTS,
                "campfire_prompt_visible",
                prompt_visible,
                create=True,
            ),
        ):
            NIGHTS.navigate_to(1, "campfire")

        self.assertEqual(prompt_visible.call_count, 2)
        self.assertEqual(observe.call_count, 1)

if __name__ == "__main__":
    unittest.main()
