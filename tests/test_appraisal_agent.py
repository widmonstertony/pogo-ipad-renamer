from __future__ import annotations

import unittest

from pogo_iphone_renamer.appraisal_agent import target_allowed


class AppraisalNavigationSafetyTests(unittest.TestCase):
    def test_detail_menu_allows_only_appraisal(self) -> None:
        self.assertTrue(target_allowed("DETAIL_MENU", "OPEN_APPRAISAL", "寶可夢鑑定", "tap"))
        self.assertFalse(target_allowed("DETAIL_MENU", "OPEN_APPRAISAL", "傳送", "tap"))

    def test_detail_goal_separates_menu_and_pencil(self) -> None:
        self.assertTrue(target_allowed("DETAIL", "OPEN_APPRAISAL", "更多選單", "tap"))
        self.assertFalse(target_allowed("DETAIL", "OPEN_APPRAISAL", "名稱鉛筆", "tap"))
        self.assertTrue(target_allowed("DETAIL", "OPEN_RENAME_DIALOG", "名稱鉛筆", "tap"))

    def test_unknown_navigation_is_rejected(self) -> None:
        self.assertFalse(target_allowed("UNKNOWN", "OPEN_APPRAISAL", "anything", "tap"))


if __name__ == "__main__":
    unittest.main()

