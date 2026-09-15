from __future__ import annotations

import base64
import io
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from pogo_iphone_renamer.live_activity import publish_preview, update_live_activity


class LiveActivityTests(unittest.TestCase):
    def test_maintenance_replaces_stale_game_context_without_counting_success(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "activity.json"
            update_live_activity({"type": "pokemon", "species": "呱呱泡蛙",
                                  "nickname": "呱呱泡❿⓮⓫⁷⁸"}, path=path)
            update_live_activity({"type": "progress", "renamed": 0,
                                  "current": 1, "phase": "processing"}, path=path)
            update_live_activity({"type": "error", "message": "旧故障"}, path=path)
            activity = update_live_activity({
                "type": "maintenance", "screen": "LOCKED",
                "message": "MCP 修复已加载；重载后设备锁屏",
                "user_action": "请解锁一次 iPad，无需手动打开游戏。",
            }, path=path)
            self.assertEqual(activity["screen"], "LOCKED")
            self.assertEqual(activity["progress"]["renamed"], 0)
            self.assertTrue(activity["attention"]["required"])
            self.assertIn("请解锁一次", activity["attention"]["user_action"])
            for key in ("pokemon", "iv", "nickname", "item_result", "waiting"):
                self.assertNotIn(key, activity)
            self.assertNotIn("旧故障", activity["step"])

    def test_inactive_ax_error_requests_service_repair_not_game_navigation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            activity = update_live_activity({
                "type": "error", "message": "MCP 辅助功能运行时未激活（axRuntimeMode: inactive）",
            }, path=Path(directory) / "activity.json")
            self.assertIn("恢复 iPad MCP", activity["attention"]["user_action"])
            self.assertIn("无需解锁或重开游戏", activity["attention"]["user_action"])
            self.assertTrue(activity["attention"]["required"])

    def test_exact_field_wait_reports_rename_dialog_not_stale_appraisal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "activity.json"
            update_live_activity({"type": "iv_measurement", "attack": 11}, path=path)
            activity = update_live_activity({
                "type": "waiting", "screen": "RENAME_DIALOG", "stage": "昵称逐字核验",
                "reason": "字段不可读", "attempt": 2, "total": 3,
                "elapsed_seconds": 60, "user_action": "不要手动点 OK",
            }, path=path)
            self.assertEqual(activity["screen"], "RENAME_DIALOG")
            self.assertEqual(activity["waiting"]["total"], 3)
            self.assertEqual(activity["waiting"]["elapsed_seconds"], 60)
            self.assertEqual(activity["iv"]["attack"], 11)
            activity = update_live_activity({"type": "progress", "current": 14, "phase": "completed"}, path=path)
            self.assertEqual(activity["screen"], "DETAIL")

    def test_detail_and_measurement_are_retained_while_steps_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "activity.json"
            update_live_activity(
                {
                    "type": "detail",
                    "species": "劈斧螳螂",
                    "current_name": "劈斧螳螂",
                    "is_default": True,
                },
                path=path,
            )
            update_live_activity(
                {"type": "iv_measurement", "attack": 15, "defense": 14, "stamina": 13},
                path=path,
            )
            activity = update_live_activity(
                {"type": "status", "message": "正在逐字核验昵称"},
                path=path,
            )

            self.assertEqual(activity["pokemon"]["name"], "劈斧螳螂")
            self.assertEqual(activity["iv"]["attack"], 15)
            self.assertEqual(activity["screen"], "APPRAISAL_BARS")
            self.assertEqual(activity["step"], "正在逐字核验昵称")
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["pokemon"]["species"], "劈斧螳螂")

    def test_preview_is_created_from_an_existing_worker_frame(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            buffer = io.BytesIO()
            Image.new("RGB", (1366, 1024), "white").save(buffer, format="JPEG")
            target = Path(directory) / "preview.jpg"

            self.assertTrue(publish_preview(base64.b64encode(buffer.getvalue()).decode(), path=target))

            with Image.open(target) as preview:
                self.assertEqual((preview.width, preview.height), (1024, 1366))
                self.assertEqual(preview.mode, "RGB")

    def test_finished_replaces_a_stale_appraisal_state_with_detail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "activity.json"
            update_live_activity(
                {"type": "iv_measurement", "attack": 15, "defense": 14, "stamina": 13},
                path=path,
            )

            activity = update_live_activity(
                {"type": "finished", "message": "已验证当前盒子末尾"}, path=path
            )

            self.assertEqual(activity["screen"], "DETAIL")
            self.assertEqual(activity["last_result"], "批量已完成")
            self.assertEqual(activity["step"], "已验证当前盒子末尾")

    def test_waiting_explains_reason_and_is_cleared_by_the_next_step(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "activity.json"
            activity = update_live_activity(
                {
                    "type": "waiting",
                    "stage": "详情身份核验",
                    "reason": "OCR 本帧为空",
                    "attempt": 4,
                    "total": 12,
                    "elapsed_seconds": 28,
                    "next_action": "读取下一帧",
                    "user_action": "无需操作",
                },
                path=path,
            )
            self.assertEqual(activity["waiting"]["attempt"], 4)
            self.assertIn("OCR 本帧为空", activity["step"])

            activity = update_live_activity(
                {"type": "status", "message": "正在打开鉴定"}, path=path
            )
            self.assertNotIn("waiting", activity)

    def test_card_completion_is_not_retained_as_a_batch_completion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "activity.json"
            update_live_activity({"type": "finished"}, path=path)

            activity = update_live_activity(
                {"type": "progress", "current": 8, "phase": "completed"},
                path=path,
            )

            self.assertNotIn("last_result", activity)
            self.assertEqual(
                activity["item_result"], "第 8 只已安全处理；正在验证翻到下一只。"
            )

    def test_new_card_clears_previous_card_detail_and_marks_identity_wait_as_detail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "activity.json"
            update_live_activity(
                {"type": "detail", "species": "小灰怪", "current_name": "小灰怪"},
                path=path,
            )
            update_live_activity(
                {"type": "iv_measurement", "attack": 1, "defense": 2, "stamina": 3},
                path=path,
            )
            update_live_activity(
                {"type": "progress", "current": 2, "phase": "processing"},
                path=path,
            )
            activity = update_live_activity(
                {"type": "waiting", "stage": "详情身份核验", "reason": "等待三帧"},
                path=path,
            )

            self.assertNotIn("pokemon", activity)
            self.assertNotIn("iv", activity)
            self.assertEqual(activity["screen"], "DETAIL")

    def test_error_includes_explicit_user_action(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "activity.json"
            activity = update_live_activity(
                {"type": "error", "message": "无法安全确认当前页面"}, path=path
            )

            self.assertTrue(activity["attention"]["required"])
            self.assertIn("安全停止", activity["attention"]["user_action"])

    def test_new_wait_clears_a_previous_error_attention(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "activity.json"
            update_live_activity({"type": "error", "message": "MCP 断开"}, path=path)
            activity = update_live_activity(
                {"type": "waiting", "stage": "等待 MCP 重连", "reason": "重试中"},
                path=path,
            )

            self.assertNotIn("attention", activity)
            self.assertEqual(activity["waiting"]["stage"], "等待 MCP 重连")


if __name__ == "__main__":
    unittest.main()
