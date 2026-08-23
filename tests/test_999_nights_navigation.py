import importlib.util
import math
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
            mock.patch.object(NIGHTS, "steer_toward", return_value=0.0),
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

    def test_character_does_not_walk_until_arrow_is_aligned(self):
        events = []
        observations = iter(
            [
                ((0.0, 0.0, math.radians(9)), (50.0, 0.0), 0.68),
                ((0.0, 0.0, 0.0), (40.0, 0.0), 0.68),
                ((0.0, 0.0, 0.0), (10.0, 0.0), 0.68),
            ]
        )

        def observe(*_args):
            events.append("observe")
            return next(observations)

        keyboard = mock.Mock()
        keyboard.press.side_effect = lambda key: events.append(f"down:{key}")
        keyboard.release.side_effect = lambda key: events.append(f"up:{key}")
        with (
            mock.patch.object(NIGHTS, "observe_target", side_effect=observe),
            mock.patch.object(NIGHTS, "sleep_check"),
            mock.patch.object(NIGHTS, "keyboard", keyboard),
            mock.patch.object(
                NIGHTS.win32api,
                "mouse_event",
                side_effect=lambda *_args: events.append("turn"),
            ),
        ):
            NIGHTS.navigate_to(1, "door")

        observations_at = [
            index for index, event in enumerate(events) if event == "observe"
        ]
        w_down_at = [
            index for index, event in enumerate(events) if event == "down:w"
        ]
        w_up_at = [
            index for index, event in enumerate(events) if event == "up:w"
        ]
        self.assertLess(events.index("turn"), w_down_at[0])
        self.assertLess(w_down_at[0], w_up_at[0])
        self.assertLess(w_up_at[0], observations_at[1])
        self.assertGreater(w_down_at[1], observations_at[1])

    def test_regular_icon_occlusion_counts_as_reaching_the_marker(self):
        observations = iter(
            [
                ((0.0, 0.0, 0.0), (30.0, 0.0), 0.68),
                ((0.0, 0.0, 0.0), None, 0.45),
            ]
        )
        observe = mock.Mock(side_effect=observations)
        keyboard = mock.Mock()
        with (
            mock.patch.object(NIGHTS, "observe_target", observe),
            mock.patch.object(NIGHTS, "steer_toward", return_value=0.0),
            mock.patch.object(NIGHTS, "sleep_check"),
            mock.patch.object(NIGHTS, "keyboard", keyboard),
        ):
            reached = NIGHTS.navigate_to(1, "door")

        self.assertTrue(reached)
        self.assertEqual(observe.call_count, 2)

    def test_walking_stops_before_correcting_a_new_heading_error(self):
        events = []
        observations = iter(
            [
                ((0.0, 0.0, 0.0), (50.0, 0.0), 0.68),
                ((0.0, 0.0, math.radians(9)), (40.0, 0.0), 0.68),
                ((0.0, 0.0, 0.0), (30.0, 0.0), 0.68),
                ((0.0, 0.0, 0.0), (10.0, 0.0), 0.68),
            ]
        )
        keyboard = mock.Mock()
        keyboard.press.side_effect = lambda key: events.append(f"down:{key}")
        keyboard.release.side_effect = lambda key: events.append(f"up:{key}")
        with (
            mock.patch.object(NIGHTS, "observe_target", side_effect=observations),
            mock.patch.object(NIGHTS, "sleep_check"),
            mock.patch.object(NIGHTS, "keyboard", keyboard),
            mock.patch.object(
                NIGHTS.win32api,
                "mouse_event",
                side_effect=lambda *_args: events.append("turn"),
            ),
        ):
            NIGHTS.navigate_to(1, "door")

        turn_at = events.index("turn")
        first_up_at = events.index("up:w")
        probe_down_at = events.index("down:w", first_up_at)
        self.assertLess(first_up_at, turn_at)
        self.assertLess(turn_at, probe_down_at)

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
            mock.patch.object(NIGHTS, "steer_toward", return_value=0.0),
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

    def test_campfire_keeps_moving_when_player_arrow_occludes_icon(self):
        observations = iter(
            [
                ((0.0, 0.0, 0.0), (40.0, 0.0), 0.85),
                ((0.0, 0.0, 0.0), (20.0, 0.0), 0.85),
                ((0.0, 0.0, 0.0), None, 0.48),
                ((0.0, 0.0, 0.0), (5.0, 0.0), 0.85),
            ]
        )
        observe = mock.Mock(side_effect=observations)
        wait_for_prompt = mock.Mock(side_effect=(False, False, True))
        events = []
        keyboard = mock.Mock()
        keyboard.press.side_effect = lambda key: events.append(("down", key))
        keyboard.release.side_effect = lambda key: events.append(("up", key))
        with (
            mock.patch.object(NIGHTS, "observe_target", observe),
            mock.patch.object(NIGHTS, "campfire_prompt_visible", return_value=False),
            mock.patch.object(
                NIGHTS,
                "wait_for_campfire_prompt",
                wait_for_prompt,
            ),
            mock.patch.object(NIGHTS, "steer_toward", return_value=0.0),
            mock.patch.object(NIGHTS, "sleep_check"),
            mock.patch.object(NIGHTS, "keyboard", keyboard),
        ):
            NIGHTS.navigate_to(1, "campfire")

        self.assertEqual(observe.call_count, 3)
        self.assertEqual(wait_for_prompt.call_count, 3)
        w_events = [event for event in events if event[1] == "w"]
        self.assertEqual(w_events, [("down", "w"), ("up", "w")])

if __name__ == "__main__":
    unittest.main()
