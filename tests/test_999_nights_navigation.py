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

    def test_sample_targets_are_fixed_from_left_to_right(self):
        self.assertEqual(
            NIGHTS.SAMPLE_TARGETS,
            ("door", "boss", "route_1", "route_2"),
        )
        crop_x_positions = [
            NIGHTS.ICON_CROPS[target][0]
            for target in NIGHTS.SAMPLE_TARGETS
        ]
        self.assertEqual(crop_x_positions, sorted(crop_x_positions))

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

    def test_dodge_holds_w_and_shift_together_long_enough_for_game_input(self):
        observations = iter(
            [
                ((0.0, 0.0, 0.0), (50.0, 0.0), 1.0),
                ((0.0, 0.0, 0.0), (10.0, 0.0), 1.0),
            ]
        )
        held_keys = set()
        held_during_sleep = []
        keyboard = mock.Mock()
        keyboard.press.side_effect = held_keys.add
        keyboard.release.side_effect = held_keys.discard

        def record_sleep(seconds):
            held_during_sleep.append((seconds, frozenset(held_keys)))

        with (
            mock.patch.object(NIGHTS, "observe_target", side_effect=observations),
            mock.patch.object(NIGHTS, "sleep_check", side_effect=record_sleep),
            mock.patch.object(NIGHTS, "keyboard", keyboard),
        ):
            NIGHTS.navigate_to(
                1,
                "route_1",
                dodge=True,
                deadline=NIGHTS.time.monotonic() + 10,
            )

        simultaneous_holds = [
            seconds
            for seconds, keys in held_during_sleep
            if {"w", "shift"}.issubset(keys)
        ]
        self.assertTrue(simultaneous_holds)
        self.assertGreaterEqual(max(simultaneous_holds), 0.05)

    def test_large_heading_error_uses_a_fast_camera_turn(self):
        mouse_event = mock.Mock()
        with mock.patch.object(NIGHTS.win32api, "mouse_event", mouse_event):
            NIGHTS.steer_toward((0.0, 0.0, 0.0), (0.0, 100.0))

        horizontal_pixels = mouse_event.call_args.args[1]
        self.assertGreaterEqual(abs(horizontal_pixels), 300)

    def test_dodge_allows_moderate_heading_error_without_turning(self):
        observations = iter(
            [
                (
                    (0.0, 0.0, math.radians(5)),
                    (50.0, 0.0),
                    0.68,
                ),
                ((0.0, 0.0, 0.0), (10.0, 0.0), 0.68),
            ]
        )
        perform_dodge = mock.Mock()
        mouse_event = mock.Mock()
        keyboard = mock.Mock()
        with (
            mock.patch.object(NIGHTS, "observe_target", side_effect=observations),
            mock.patch.object(NIGHTS, "perform_dodge", perform_dodge),
            mock.patch.object(NIGHTS, "sleep_check"),
            mock.patch.object(NIGHTS, "keyboard", keyboard),
            mock.patch.object(NIGHTS.win32api, "mouse_event", mouse_event),
        ):
            NIGHTS.navigate_to(1, "route_1", dodge=True)

        self.assertAlmostEqual(
            math.degrees(NIGHTS.MOVE_ALIGNMENT_TOLERANCE),
            3.0,
        )
        self.assertAlmostEqual(
            math.degrees(NIGHTS.DODGE_ALIGNMENT_TOLERANCE),
            6.0,
        )
        perform_dodge.assert_called_once_with()
        mouse_event.assert_not_called()

    def test_regular_navigation_still_corrects_moderate_heading_error(self):
        observations = iter(
            [
                (
                    (0.0, 0.0, math.radians(5)),
                    (50.0, 0.0),
                    0.68,
                ),
                ((0.0, 0.0, 0.0), (40.0, 0.0), 0.68),
                ((0.0, 0.0, 0.0), (10.0, 0.0), 0.68),
            ]
        )
        mouse_event = mock.Mock()
        keyboard = mock.Mock()
        with (
            mock.patch.object(NIGHTS, "observe_target", side_effect=observations),
            mock.patch.object(NIGHTS, "sleep_check"),
            mock.patch.object(NIGHTS, "keyboard", keyboard),
            mock.patch.object(NIGHTS.win32api, "mouse_event", mouse_event),
        ):
            NIGHTS.navigate_to(1, "door")

        mouse_event.assert_called_once()

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

    def test_dodge_icon_occlusion_uses_current_arrow_against_last_icon_position(self):
        observations = iter(
            [
                ((0.0, 0.0, 0.0), (65.0, 0.0), 0.68),
                ((65.0, 0.0, 0.0), None, 0.44),
            ]
        )
        observe = mock.Mock(side_effect=observations)
        keyboard = mock.Mock()
        with (
            mock.patch.object(NIGHTS, "observe_target", observe),
            mock.patch.object(NIGHTS, "sleep_check"),
            mock.patch.object(NIGHTS, "keyboard", keyboard),
        ):
            reached = NIGHTS.navigate_to(1, "route_2", dodge=True)

        self.assertTrue(reached)
        self.assertEqual(observe.call_count, 2)

    def test_dodge_crossing_counts_when_moving_icon_disappears_after_one_dodge(self):
        observations = iter(
            [
                ((0.0, 0.0, 0.0), (72.8, 0.0), 0.68),
                ((0.0, 0.0, 0.0), None, 0.41),
            ]
        )
        observe = mock.Mock(side_effect=observations)
        keyboard = mock.Mock()
        with (
            mock.patch.object(NIGHTS, "observe_target", observe),
            mock.patch.object(NIGHTS, "sleep_check"),
            mock.patch.object(NIGHTS, "keyboard", keyboard),
        ):
            reached = NIGHTS.navigate_to(1, "route_2", dodge=True)

        self.assertTrue(reached)
        self.assertEqual(observe.call_count, 2)

    def test_dodge_does_not_assume_crossing_when_lost_icon_was_too_far_away(self):
        observations = iter(
            [
                ((0.0, 0.0, 0.0), (120.0, 0.0), 0.68),
                ((0.0, 0.0, 0.0), None, 0.41),
                ((0.0, 0.0, 0.0), (10.0, 0.0), 0.68),
            ]
        )
        observe = mock.Mock(side_effect=observations)
        keyboard = mock.Mock()
        with (
            mock.patch.object(NIGHTS, "observe_target", observe),
            mock.patch.object(NIGHTS, "sleep_check"),
            mock.patch.object(NIGHTS, "keyboard", keyboard),
        ):
            reached = NIGHTS.navigate_to(1, "route_2", dodge=True)

        self.assertTrue(reached)
        self.assertEqual(observe.call_count, 3)

    def test_low_confidence_far_from_last_icon_does_not_count_as_arrival(self):
        observations = iter(
            [
                ((0.0, 0.0, 0.0), (65.0, 0.0), 0.68),
                ((10.0, 0.0, 0.0), None, 0.44),
                ((65.0, 0.0, 0.0), (65.0, 0.0), 0.68),
            ]
        )
        observe = mock.Mock(side_effect=observations)
        keyboard = mock.Mock()
        with (
            mock.patch.object(NIGHTS, "observe_target", observe),
            mock.patch.object(NIGHTS, "sleep_check"),
            mock.patch.object(NIGHTS, "keyboard", keyboard),
        ):
            reached = NIGHTS.navigate_to(1, "route_2", dodge=True)

        self.assertTrue(reached)
        self.assertEqual(observe.call_count, 3)

    def test_dodge_interval_is_point_thirty_five_seconds(self):
        self.assertAlmostEqual(NIGHTS.DODGE_SETTLE_SECONDS, 0.35)

    def test_route_navigation_returns_for_fallback_after_two_seconds_lost(self):
        observations = iter(
            [
                ((0.0, 0.0, 0.0), None, 0.40),
                ((0.0, 0.0, 0.0), None, 0.40),
            ]
        )
        keyboard = mock.Mock()
        with (
            mock.patch.object(NIGHTS, "observe_target", side_effect=observations),
            mock.patch.object(
                NIGHTS.time,
                "monotonic",
                side_effect=(10.0, 12.0),
            ),
            mock.patch.object(NIGHTS, "sleep_check"),
            mock.patch.object(NIGHTS, "keyboard", keyboard),
        ):
            reached = NIGHTS.navigate_to(
                1,
                "route_1",
                dodge=True,
                target_lost_timeout=2.0,
            )

        self.assertFalse(reached)

    def test_route_navigation_ignores_brief_icon_loss(self):
        observations = iter(
            [
                ((0.0, 0.0, 0.0), None, 0.40),
                ((0.0, 0.0, 0.0), (10.0, 0.0), 0.68),
            ]
        )
        observe = mock.Mock(side_effect=observations)
        keyboard = mock.Mock()
        with (
            mock.patch.object(NIGHTS, "observe_target", observe),
            mock.patch.object(NIGHTS.time, "monotonic", return_value=10.0),
            mock.patch.object(NIGHTS, "sleep_check"),
            mock.patch.object(NIGHTS, "keyboard", keyboard),
        ):
            reached = NIGHTS.navigate_to(
                1,
                "route_1",
                dodge=True,
                target_lost_timeout=2.0,
            )

        self.assertTrue(reached)
        self.assertEqual(observe.call_count, 2)

    def test_marker_arrival_advances_for_shared_crossing_duration(self):
        observations = iter(
            [
                ((0.0, 0.0, 0.0), (50.0, 0.0), 0.68),
                ((0.0, 0.0, 0.0), (10.0, 0.0), 0.68),
            ]
        )
        sleeps = []
        keyboard = mock.Mock()
        with (
            mock.patch.object(NIGHTS, "observe_target", side_effect=observations),
            mock.patch.object(NIGHTS, "sleep_check", side_effect=sleeps.append),
            mock.patch.object(NIGHTS, "keyboard", keyboard),
        ):
            NIGHTS.navigate_to(1, "door")

        self.assertIn(0.30, sleeps)

    def test_dodge_loop_finishes_current_route_after_sixty_seconds(self):
        navigate = mock.Mock(return_value=True)
        with (
            mock.patch.object(
                NIGHTS.time,
                "monotonic",
                side_effect=(0.0, 59.0, 61.0),
            ),
            mock.patch.object(NIGHTS, "navigate_to", navigate),
            mock.patch.object(NIGHTS, "release_movement_keys"),
        ):
            NIGHTS.run_dodge_loop(1)

        navigate.assert_called_once_with(
            1,
            "route_1",
            dodge=True,
            target_lost_timeout=2.0,
        )

    def test_dodge_loop_returns_to_same_route_after_boss_fallback(self):
        navigate = mock.Mock(side_effect=(False, True, True))
        with (
            mock.patch.object(
                NIGHTS.time,
                "monotonic",
                side_effect=(0.0, 59.0, 61.0),
            ),
            mock.patch.object(NIGHTS, "navigate_to", navigate),
            mock.patch.object(NIGHTS, "release_movement_keys"),
        ):
            NIGHTS.run_dodge_loop(1)

        self.assertEqual(
            navigate.call_args_list,
            [
                mock.call(
                    1,
                    "route_1",
                    dodge=True,
                    target_lost_timeout=2.0,
                ),
                mock.call(1, "boss", dodge=True),
                mock.call(
                    1,
                    "route_1",
                    dodge=True,
                    target_lost_timeout=2.0,
                ),
            ],
        )

    def test_rest_does_not_back_away_after_successful_click(self):
        events = []
        keyboard = mock.Mock()
        keyboard.press.side_effect = lambda key: events.append(("down", key))
        keyboard.release.side_effect = lambda key: events.append(("up", key))
        keyboard.press_and_release.side_effect = (
            lambda key: events.append(("tap", key))
        )
        with (
            mock.patch.object(
                NIGHTS,
                "wait_for_ui",
                side_effect=((10, 10), (20, 20)),
            ),
            mock.patch.object(NIGHTS, "click_window_at"),
            mock.patch.object(
                NIGHTS,
                "sleep_check",
                side_effect=lambda seconds: events.append(("sleep", seconds)),
            ),
            mock.patch.object(NIGHTS, "keyboard", keyboard),
        ):
            NIGHTS.rest_and_refresh(1)

        movement_events = [
            event
            for event in events
            if event[0] in {"down", "up"} and event[1] in {"s", "w"}
        ]
        self.assertEqual(movement_events, [])

    def test_rest_backs_up_and_retries_f_when_button_does_not_appear(self):
        events = []
        keyboard = mock.Mock()
        keyboard.press.side_effect = lambda key: events.append(("down", key))
        keyboard.release.side_effect = lambda key: events.append(("up", key))
        keyboard.press_and_release.side_effect = (
            lambda key: events.append(("tap", key))
        )
        wait_for_ui = mock.Mock(
            side_effect=((10, 10), None, (11, 11), (20, 20))
        )
        with (
            mock.patch.object(NIGHTS, "wait_for_ui", wait_for_ui),
            mock.patch.object(NIGHTS, "click_window_at"),
            mock.patch.object(
                NIGHTS,
                "sleep_check",
                side_effect=lambda seconds: events.append(("sleep", seconds)),
            ),
            mock.patch.object(NIGHTS, "keyboard", keyboard),
        ):
            NIGHTS.rest_and_refresh(1)

        self.assertEqual(
            wait_for_ui.call_args_list[:4],
            [
                mock.call(1, "press_f.png", timeout=8.0),
                mock.call(1, "mouse_click.png", timeout=2.0),
                mock.call(1, "press_f.png", timeout=2.0),
                mock.call(1, "mouse_click.png", timeout=2.0),
            ],
        )
        f_taps = [
            index
            for index, event in enumerate(events)
            if event == ("tap", "f")
        ]
        first_back = events.index(("down", "s"))
        s_downs = [event for event in events if event == ("down", "s")]
        w_downs = [event for event in events if event == ("down", "w")]
        self.assertEqual(len(f_taps), 2)
        self.assertEqual(len(s_downs), 1)
        self.assertEqual(len(w_downs), 1)
        self.assertLess(f_taps[0], first_back)
        self.assertLess(first_back, f_taps[1])

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
        self.assertAlmostEqual(
            NIGHTS.CAMPFIRE_OCCLUSION_STEP_SECONDS
            * NIGHTS.CAMPFIRE_OCCLUSION_STEPS,
            0.20,
        )
        observations = iter(
            [
                ((0.0, 0.0, 0.0), (60.0, 0.0), 0.85),
                ((0.0, 0.0, 0.0), (40.0, 0.0), 0.85),
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

    def test_large_right_hand_error_turns_left_when_asked_to(self):
        mouse_event = mock.Mock()
        with mock.patch.object(NIGHTS.win32api, "mouse_event", mouse_event):
            NIGHTS.steer_toward((0.0, 0.0, 0.0), (0.0, 100.0), prefer_left=True)

        self.assertLess(mouse_event.call_args.args[1], 0)

    def test_large_right_hand_error_turns_right_by_default(self):
        mouse_event = mock.Mock()
        with mock.patch.object(NIGHTS.win32api, "mouse_event", mouse_event):
            NIGHTS.steer_toward((0.0, 0.0, 0.0), (0.0, 100.0))

        self.assertGreater(mouse_event.call_args.args[1], 0)

    def test_small_right_hand_error_still_turns_right(self):
        mouse_event = mock.Mock()
        error = NIGHTS.RIGHT_TURN_MAX_ERROR * 0.5
        target = (math.cos(error) * 100.0, math.sin(error) * 100.0)
        with mock.patch.object(NIGHTS.win32api, "mouse_event", mouse_event):
            NIGHTS.steer_toward((0.0, 0.0, 0.0), target)

        self.assertGreater(mouse_event.call_args.args[1], 0)

    def test_campfire_navigation_gives_up_after_the_lost_limit(self):
        pose = (0.0, 0.0, 0.0)
        observe = mock.Mock(return_value=(pose, None, 0.20))
        keyboard = mock.Mock()
        with (
            mock.patch.object(NIGHTS, "observe_target", observe),
            mock.patch.object(NIGHTS, "campfire_prompt_visible", return_value=False),
            mock.patch.object(NIGHTS, "steer_toward", return_value=0.0),
            mock.patch.object(NIGHTS, "sleep_check"),
            mock.patch.object(NIGHTS, "keyboard", keyboard),
        ):
            reached = NIGHTS.navigate_to(
                1, "campfire", target_lost_limit=NIGHTS.CAMPFIRE_LOST_LIMIT
            )

        self.assertFalse(reached)
        self.assertEqual(observe.call_count, NIGHTS.CAMPFIRE_LOST_LIMIT)

    def test_rest_backs_away_only_once_before_searching_again(self):
        ui_results = iter(
            [
                (10, 10),   # first press_f prompt
                None,       # no rest button
                (10, 10),   # press_f still visible after backing away
                None,       # still no rest button
                (10, 10),   # press_f prompt after re-navigating
                (20, 20),   # rest button
            ]
        )
        back_away = mock.Mock()
        navigate = mock.Mock(return_value=True)
        with (
            mock.patch.object(NIGHTS, "wait_for_ui", side_effect=ui_results),
            mock.patch.object(NIGHTS, "back_away_from_campfire", back_away),
            mock.patch.object(NIGHTS, "navigate_to_campfire", navigate),
            mock.patch.object(NIGHTS, "click_window_at"),
            mock.patch.object(NIGHTS, "tap_key"),
            mock.patch.object(NIGHTS, "hold_key_for"),
            mock.patch.object(NIGHTS, "sleep_check"),
        ):
            NIGHTS.rest_and_refresh(1)

        self.assertEqual(back_away.call_count, 1)
        self.assertEqual(navigate.call_count, 1)

    def test_campfire_helper_runs_to_the_door_before_retrying(self):
        calls = []

        def fake_navigate(_hwnd, target_name, **kwargs):
            calls.append(target_name)
            if target_name == "campfire":
                return calls.count("campfire") >= 2
            return True

        with mock.patch.object(NIGHTS, "navigate_to", side_effect=fake_navigate):
            NIGHTS.navigate_to_campfire(1)

        self.assertEqual(calls, ["campfire", "door", "campfire"])


    def test_route_step_uses_a_timeout_and_lost_limit(self):
        navigate = mock.Mock(return_value=True)
        with mock.patch.object(NIGHTS, "navigate_to", navigate):
            NIGHTS.navigate_step(1, "door")

        kwargs = navigate.call_args.kwargs
        self.assertEqual(kwargs["target_lost_limit"], NIGHTS.ROUTE_LOST_LIMIT)
        self.assertEqual(kwargs["move_timeout"], NIGHTS.ROUTE_STEP_TIMEOUT)
        self.assertNotIn("deadline", kwargs)

    def test_campfire_step_keeps_its_own_lost_limit(self):
        navigate = mock.Mock(return_value=True)
        with mock.patch.object(NIGHTS, "navigate_to", navigate):
            NIGHTS.navigate_step(1, "campfire")

        self.assertEqual(
            navigate.call_args.kwargs["target_lost_limit"],
            NIGHTS.CAMPFIRE_LOST_LIMIT,
        )

    def test_failed_step_backtracks_to_the_previous_marker(self):
        calls = []

        def fake_step(_hwnd, target_name, **_kwargs):
            calls.append(target_name)
            return not (target_name == "boss" and calls.count("boss") == 1)

        with mock.patch.object(NIGHTS, "navigate_step", side_effect=fake_step):
            NIGHTS.navigate_with_backtrack(1, "boss", "route_1")

        self.assertEqual(calls, ["boss", "route_1", "boss"])

    def test_failed_backtrack_does_not_step_back_any_further(self):
        calls = []

        def fake_step(_hwnd, target_name, **_kwargs):
            # The door and the boss both fail once; backtracking must not
            # cascade further back, it just retries the door.
            calls.append(target_name)
            return len(calls) >= 3

        with mock.patch.object(NIGHTS, "navigate_step", side_effect=fake_step):
            NIGHTS.navigate_with_backtrack(1, "door", "boss")

        self.assertEqual(calls, ["door", "boss", "door"])

    def test_campfire_backtrack_sidesteps_left_before_the_last_door_try(self):
        calls = []
        keys = []

        def fake_step(_hwnd, target_name, **kwargs):
            calls.append((target_name, kwargs.get("prefer_left", False)))
            if target_name == "door":
                return False
            return [name for name, _ in calls].count("campfire") >= 2

        with (
            mock.patch.object(NIGHTS, "navigate_step", side_effect=fake_step),
            mock.patch.object(
                NIGHTS,
                "hold_key_for",
                side_effect=lambda key, seconds: keys.append((key, seconds)),
            ),
        ):
            NIGHTS.navigate_to_campfire(1)

        self.assertEqual(
            calls,
            [
                ("campfire", True),
                ("door", True),
                ("door", True),
                ("campfire", True),
            ],
        )
        self.assertEqual(keys, [("a", NIGHTS.CAMPFIRE_BACK_AWAY_SECONDS)])

    def test_move_timeout_starts_only_after_the_first_turn(self):
        observations = iter(
            [
                ((0.0, 0.0, 0.0), (50.0, 50.0), 1.0),
                ((0.0, 0.0, 0.0), (50.0, 50.0), 1.0),
                ((0.0, 0.0, 0.0), (50.0, 50.0), 1.0),
                ((0.0, 0.0, 0.0), (50.0, 0.0), 1.0),
                ((0.0, 0.0, 0.0), (50.0, 0.0), 1.0),
            ]
        )
        observe = mock.Mock(side_effect=observations)
        keyboard = mock.Mock()
        with (
            mock.patch.object(NIGHTS, "observe_target", observe),
            mock.patch.object(NIGHTS, "steer_toward", return_value=0.0),
            mock.patch.object(NIGHTS, "hold_key_for"),
            mock.patch.object(NIGHTS, "sleep_check"),
            mock.patch.object(NIGHTS, "keyboard", keyboard),
        ):
            result = NIGHTS.navigate_to(1, "door", move_timeout=0.0)

        # The three turning frames must not burn the movement budget; the
        # deadline only starts on the first aligned frame.
        self.assertFalse(result)
        self.assertEqual(observe.call_count, 4)

    def test_missing_player_arrow_nudges_the_camera(self):
        frames = [(None, None, 0.0)] * NIGHTS.POSE_LOST_NUDGE_INTERVAL
        frames.append(((0.0, 0.0, 0.0), (10.0, 0.0), 1.0))
        observe = mock.Mock(side_effect=iter(frames))
        mouse_event = mock.Mock()
        keyboard = mock.Mock()
        with (
            mock.patch.object(NIGHTS, "observe_target", observe),
            mock.patch.object(NIGHTS.win32api, "mouse_event", mouse_event),
            mock.patch.object(NIGHTS, "sleep_check"),
            mock.patch.object(NIGHTS, "keyboard", keyboard),
        ):
            reached = NIGHTS.navigate_to(1, "door")

        self.assertTrue(reached)
        self.assertEqual(mouse_event.call_count, 1)
        self.assertEqual(
            abs(mouse_event.call_args.args[1]), NIGHTS.POSE_NUDGE_PIXELS
        )

    def test_missing_player_arrow_gives_up_after_the_lost_limit(self):
        observe = mock.Mock(return_value=(None, None, 0.0))
        mouse_event = mock.Mock()
        keyboard = mock.Mock()
        with (
            mock.patch.object(NIGHTS, "observe_target", observe),
            mock.patch.object(NIGHTS.win32api, "mouse_event", mouse_event),
            mock.patch.object(NIGHTS, "sleep_check"),
            mock.patch.object(NIGHTS, "keyboard", keyboard),
        ):
            reached = NIGHTS.navigate_to(1, "door")

        self.assertFalse(reached)
        self.assertEqual(observe.call_count, NIGHTS.POSE_LOST_LIMIT)
        nudges = [call.args[1] for call in mouse_event.call_args_list]
        self.assertEqual(
            len(nudges), NIGHTS.POSE_LOST_LIMIT // NIGHTS.POSE_LOST_NUDGE_INTERVAL
        )
        # The nudges alternate so the camera does not drift in one direction.
        self.assertEqual(nudges[0], -nudges[1])

    def test_recovered_player_arrow_resets_the_lost_counter(self):
        frames = []
        for _ in range(3):
            frames.extend([(None, None, 0.0)] * (NIGHTS.POSE_LOST_LIMIT - 1))
            frames.append(((0.0, 0.0, 0.0), (50.0, 0.0), 1.0))
        frames.append(((0.0, 0.0, 0.0), (10.0, 0.0), 1.0))
        observe = mock.Mock(side_effect=iter(frames))
        keyboard = mock.Mock()
        with (
            mock.patch.object(NIGHTS, "observe_target", observe),
            mock.patch.object(NIGHTS.win32api, "mouse_event"),
            mock.patch.object(NIGHTS, "steer_toward", return_value=0.0),
            mock.patch.object(NIGHTS, "sleep_check"),
            mock.patch.object(NIGHTS, "keyboard", keyboard),
        ):
            reached = NIGHTS.navigate_to(1, "door")

        self.assertTrue(reached)
        self.assertEqual(observe.call_count, len(frames))

    def test_dodge_loop_reports_the_last_reached_route_marker(self):
        with (
            mock.patch.object(NIGHTS, "navigate_to", return_value=True),
            mock.patch.object(NIGHTS, "release_movement_keys"),
            mock.patch.object(NIGHTS, "DODGE_DURATION_SECONDS", 0.0),
        ):
            self.assertIsNone(NIGHTS.run_dodge_loop(1))

        with (
            mock.patch.object(NIGHTS, "navigate_to", return_value=True),
            mock.patch.object(NIGHTS, "release_movement_keys"),
            mock.patch.object(NIGHTS, "DODGE_DURATION_SECONDS", 0.05),
        ):
            self.assertIn(NIGHTS.run_dodge_loop(1), ("route_1", "route_2"))

if __name__ == "__main__":
    unittest.main()
