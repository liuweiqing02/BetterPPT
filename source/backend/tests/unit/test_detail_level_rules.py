from __future__ import annotations

import unittest

from app.core.constants import DETAIL_LEVEL_PAGE_RANGE
from app.services.task_service import _estimate_page_count


class DetailLevelRulesTestCase(unittest.TestCase):
    def test_range_order_is_monotonic(self) -> None:
        concise = DETAIL_LEVEL_PAGE_RANGE['concise']
        balanced = DETAIL_LEVEL_PAGE_RANGE['balanced']
        detailed = DETAIL_LEVEL_PAGE_RANGE['detailed']

        self.assertLess(concise[0], balanced[0])
        self.assertLess(balanced[0], detailed[0])
        self.assertLess(concise[1], balanced[1])
        self.assertLess(balanced[1], detailed[1])

    def test_estimated_page_count_is_monotonic(self) -> None:
        concise = _estimate_page_count('concise')
        balanced = _estimate_page_count('balanced')
        detailed = _estimate_page_count('detailed')

        self.assertLess(concise, balanced)
        self.assertLess(balanced, detailed)

        c_low, c_high = DETAIL_LEVEL_PAGE_RANGE['concise']
        b_low, b_high = DETAIL_LEVEL_PAGE_RANGE['balanced']
        d_low, d_high = DETAIL_LEVEL_PAGE_RANGE['detailed']
        self.assertGreaterEqual(concise, c_low)
        self.assertLessEqual(concise, c_high)
        self.assertGreaterEqual(balanced, b_low)
        self.assertLessEqual(balanced, b_high)
        self.assertGreaterEqual(detailed, d_low)
        self.assertLessEqual(detailed, d_high)


if __name__ == '__main__':
    unittest.main()
