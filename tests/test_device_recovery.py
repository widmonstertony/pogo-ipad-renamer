from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock
from unittest.mock import patch

from pogo_iphone_renamer import device_recovery as recovery
from pogo_iphone_renamer.appraisal_agent import Snapshot
from pogo_iphone_renamer.device_recovery import (
    DeviceLockRecoveryRequired,
    device_screen_state,
    refresh_game_foreground_capture,
    restart_game_for_capture,
    wait_for_capture_channel,
    wait_for_manual_unlock,
    wait_for_unlocked_snapshot,
)
from pogo_iphone_renamer.policy import Observation, PolicyViolation


class _Proxy:
    def __init__(self, structured: dict) -> None:
        self.structured = structured

    def call_tool(self, name: str, arguments: dict) -> dict:
        self.last_call = (name, arguments)
        return {"structuredContent": self.structured}


class CaptureStateTests(unittest.TestCase):
    def test_nonblack_lock_screen_waits_for_manual_unlock(self) -> None:
        proxy = mock.Mock()
        locked = Snapshot(text="输入密码", image="nonblack-lock-screen")
        unlocked = Snapshot(text="Pokemon GO HP kg", image="game-frame")
        with mock.patch.object(
            recovery, "device_screen_state", side_effect=[(True, True), (False, True)]
        ), mock.patch.object(recovery.time, "sleep"), mock.patch.object(
            recovery, "screen_snapshot", return_value=unlocked
        ) as refresh:
            result = wait_for_unlocked_snapshot(proxy, locked)
        self.assertIs(result, unlocked)
        refresh.assert_called_once_with(proxy)

    def test_mutating_caller_must_reenter_after_unlock(self) -> None:
        proxy = mock.Mock()
        locked = Snapshot(text="输入密码", image="nonblack-lock-screen")
        unlocked = Snapshot(text="Pokemon GO HP kg", image="game-frame")
        with mock.patch.object(
            recovery, "device_screen_state", side_effect=[(True, True), (False, True)]
        ), mock.patch.object(recovery.time, "sleep"), mock.patch.object(
            recovery, "screen_snapshot", return_value=unlocked
        ):
            with self.assertRaises(DeviceLockRecoveryRequired) as raised:
                wait_for_unlocked_snapshot(
                    proxy, locked, require_safe_reentry=True
                )

        self.assertIs(raised.exception.snapshot, unlocked)

    def test_initial_locked_black_frame_waits_then_returns_visible_frame(self) -> None:
        black = SimpleNamespace(image="black")
        visible = SimpleNamespace(image="visible")
        proxy = object()
        with patch(
            "pogo_iphone_renamer.device_recovery.navigation.snapshot_is_black",
            return_value=False,
        ), patch(
            "pogo_iphone_renamer.device_recovery.device_screen_state",
            return_value=(True, True),
        ), patch(
            "pogo_iphone_renamer.device_recovery.wait_for_manual_unlock"
        ) as wait, patch(
            "pogo_iphone_renamer.device_recovery.screen_snapshot",
            return_value=visible,
        ), patch(
            "pogo_iphone_renamer.device_recovery.emit"
        ):
            returned = wait_for_capture_channel(proxy, black)

        self.assertIs(returned, visible)
        wait.assert_called_once_with(proxy)

    def test_black_capture_without_restart_permission_never_leaves_game(self) -> None:
        black = SimpleNamespace(image="black")
        proxy = object()
        with patch(
            "pogo_iphone_renamer.device_recovery.wait_for_unlocked_snapshot",
            return_value=black,
        ), patch(
            "pogo_iphone_renamer.device_recovery.navigation.snapshot_is_black",
            return_value=True,
        ), patch(
            "pogo_iphone_renamer.device_recovery.time.monotonic",
            side_effect=[0.0, 12.1],
        ), patch(
            "pogo_iphone_renamer.device_recovery.refresh_game_foreground_capture"
        ) as refresh, patch(
            "pogo_iphone_renamer.device_recovery.restart_game_for_capture"
        ) as restart, patch("pogo_iphone_renamer.device_recovery.emit"):
            with self.assertRaisesRegex(PolicyViolation, "未返回主屏幕"):
                wait_for_capture_channel(proxy, black, allow_game_restart=False)

        refresh.assert_not_called()
        restart.assert_not_called()

    def test_persistent_headless_capture_wait_recovers_without_any_write(self) -> None:
        black = SimpleNamespace(image="black")
        recovered = SimpleNamespace(image="recovered")
        proxy = object()
        with patch(
            "pogo_iphone_renamer.device_recovery.wait_for_unlocked_snapshot",
            return_value=black,
        ), patch(
            "pogo_iphone_renamer.device_recovery.navigation.snapshot_is_black",
            side_effect=[True, False],
        ), patch(
            "pogo_iphone_renamer.device_recovery.time.monotonic",
            side_effect=[0.0, 12.1],
        ), patch(
            "pogo_iphone_renamer.device_recovery._persist_capture_wait_enabled",
            return_value=True,
        ), patch(
            "pogo_iphone_renamer.device_recovery.base._next_snapshot",
            return_value=recovered,
        ) as read, patch(
            "pogo_iphone_renamer.device_recovery.refresh_game_foreground_capture"
        ) as refresh, patch(
            "pogo_iphone_renamer.device_recovery.restart_game_for_capture"
        ) as restart, patch("pogo_iphone_renamer.device_recovery.emit"):
            returned = wait_for_capture_channel(proxy, black, allow_game_restart=False)

        self.assertIs(returned, recovered)
        read.assert_called_once_with(proxy, 5.0)
        refresh.assert_not_called()
        restart.assert_not_called()

    def test_default_manual_unlock_wait_has_no_time_limit(self) -> None:
        proxy = object()
        with patch(
            "pogo_iphone_renamer.device_recovery.device_screen_state",
            side_effect=[(True, True), (False, True)],
        ) as state, patch(
            "pogo_iphone_renamer.device_recovery.time.monotonic"
        ) as monotonic, patch(
            "pogo_iphone_renamer.device_recovery.time.sleep"
        ) as sleep, patch(
            "pogo_iphone_renamer.device_recovery.emit"
        ):
            wait_for_manual_unlock(proxy)

        self.assertEqual(state.call_count, 2)
        sleep.assert_called_once_with(1.0)
        monotonic.assert_not_called()

    def test_manual_unlock_wait_is_read_only_and_resumes(self) -> None:
        proxy = object()
        with patch(
            "pogo_iphone_renamer.device_recovery.device_screen_state",
            side_effect=[(True, True), (False, True)],
        ) as state, patch(
            "pogo_iphone_renamer.device_recovery.time.monotonic",
            side_effect=[0.0, 0.0, 1.0],
        ), patch(
            "pogo_iphone_renamer.device_recovery.time.sleep"
        ) as sleep, patch(
            "pogo_iphone_renamer.device_recovery.emit"
        ) as emit:
            wait_for_manual_unlock(proxy, timeout=10.0)

        self.assertEqual(state.call_count, 2)
        sleep.assert_called_once_with(1.0)
        self.assertIn("自动继续", emit.call_args_list[0].kwargs["message"])
        self.assertIn("已解锁", emit.call_args_list[-1].kwargs["message"])

    def test_nested_locked_state_is_read(self) -> None:
        proxy = _Proxy(
            {"device_state": {"locked": True, "screen_on": False}}
        )
        self.assertEqual(device_screen_state(proxy), (True, False))
        self.assertEqual(proxy.last_call, ("get_screen_info", {}))

    def test_unlocked_screen_is_read(self) -> None:
        proxy = _Proxy({"locked": False, "screen_on": True})
        self.assertEqual(device_screen_state(proxy), (False, True))

    def test_capture_refresh_uses_home_then_configured_game_only(self) -> None:
        class RecoveryProxy:
            pending_name = None
            settings = SimpleNamespace(
                pokemon_go_bundle_id="com.nianticlabs.pokemongo"
            )
            observation = Observation("game-token", 0.0, "Pokemon GO", 1024, 1366)

            def __init__(self) -> None:
                self.calls: list[tuple[str, dict]] = []

            def call_tool(self, name: str, arguments: dict) -> dict:
                self.calls.append((name, arguments))
                if name == "wake_and_home":
                    self.observation = Observation(
                        "home-token", 0.0, "Home", 1024, 1366
                    )
                return {}

        proxy = RecoveryProxy()
        with patch(
            "pogo_iphone_renamer.device_recovery.base._next_snapshot",
            return_value=SimpleNamespace(image="visible-home"),
        ), patch(
            "pogo_iphone_renamer.device_recovery.navigation.snapshot_is_black",
            return_value=False,
        ):
            refresh_game_foreground_capture(proxy)

        self.assertEqual([name for name, _ in proxy.calls], ["wake_and_home", "launch_app"])
        self.assertEqual(proxy.calls[0][1]["sequence"], "home_twice")
        self.assertEqual(proxy.calls[0][1]["_observation_token"], "game-token")
        self.assertEqual(proxy.calls[1][1]["_observation_token"], "home-token")
        self.assertEqual(
            proxy.calls[1][1]["bundle_id"], "com.nianticlabs.pokemongo"
        )

    def test_force_restart_targets_only_configured_game(self) -> None:
        class RecoveryProxy:
            pending_name = None
            settings = SimpleNamespace(
                pokemon_go_bundle_id="com.nianticlabs.pokemongo"
            )
            observation = Observation("before-kill", 0.0, "Pokemon GO", 1024, 1366)

            def __init__(self) -> None:
                self.calls: list[tuple[str, dict]] = []

            def call_tool(self, name: str, arguments: dict) -> dict:
                self.calls.append((name, arguments))
                if name == "kill_app":
                    self.observation = Observation(
                        "after-kill", 0.0, "Home", 1024, 1366
                    )
                return {}

        proxy = RecoveryProxy()
        restart_game_for_capture(proxy)

        self.assertEqual([name for name, _ in proxy.calls], ["kill_app", "launch_app"])
        self.assertTrue(
            all(
                arguments["bundle_id"] == "com.nianticlabs.pokemongo"
                for _name, arguments in proxy.calls
            )
        )

    def test_global_black_capture_never_sends_power_key(self) -> None:
        class RecoveryProxy:
            pending_name = None
            settings = SimpleNamespace(
                pokemon_go_bundle_id="com.nianticlabs.pokemongo"
            )
            observation = Observation("game-token", 0.0, "Pokemon GO", 1024, 1366)

            def __init__(self) -> None:
                self.calls: list[tuple[str, dict]] = []

            def call_tool(self, name: str, arguments: dict) -> dict:
                self.calls.append((name, arguments))
                if name == "wake_and_home":
                    self.observation = Observation(
                        f"home-{len(self.calls)}", 0.0, "Home", 1024, 1366
                    )
                return {}

        proxy = RecoveryProxy()
        with patch(
            "pogo_iphone_renamer.device_recovery.base._next_snapshot",
            return_value=SimpleNamespace(image="black"),
        ), patch(
            "pogo_iphone_renamer.device_recovery.navigation.snapshot_is_black",
            return_value=True,
        ), patch(
            "pogo_iphone_renamer.device_recovery.emit"
        ):
            refresh_game_foreground_capture(proxy)

        self.assertEqual(
            [name for name, _ in proxy.calls],
            ["wake_and_home", "launch_app"],
        )
        self.assertEqual(proxy.calls[0][1]["sequence"], "home_twice")
        self.assertTrue(
            all(
                arguments.get("sequence") != "power_then_home"
                for _name, arguments in proxy.calls
            )
        )


if __name__ == "__main__":
    unittest.main()
