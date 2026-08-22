from __future__ import annotations

import unittest

from pogo_iphone_renamer.gui_appraisal import friendly_appraisal_event


class AppraisalGuiLogTests(unittest.TestCase):
    def test_pokemon_result_is_compact(self) -> None:
        line = (
            '{"type":"pokemon","species":"偷兒狐","attack":15,'
            '"defense":3,"stamina":4,"percent":49,'
            '"nickname":"偷兒狐⓯❸❹⁴⁹","confidence":0.98}'
        )
        message = friendly_appraisal_event(line)
        self.assertIn("偷兒狐", message)
        self.assertIn("15/3/4", message)
        self.assertIn("偷兒狐⓯❸❹⁴⁹", message)

    def test_internal_tool_events_are_hidden(self) -> None:
        self.assertIsNone(friendly_appraisal_event('{"type":"tool","name":"screenshot"}'))


if __name__ == "__main__":
    unittest.main()

