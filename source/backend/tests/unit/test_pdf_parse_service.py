from __future__ import annotations

import unittest

from app.services.pdf_parse_service import _extract_table_blocks


class PDFParseServiceTestCase(unittest.TestCase):
    def test_extract_table_blocks_skips_separator_rows_and_normalizes_width(self) -> None:
        lines = [
            "Metric | Q1 | Q2 | Q3",
            "------ | --- | --- | ---",
            "Revenue | 10 | 12 | 15",
            "Profit | 2 | 3 | 4",
            "Note line between blocks",
            "Region    Users    Growth",
            "CN        1200     12%",
            "US        900      8%",
        ]

        blocks = _extract_table_blocks(lines)

        self.assertEqual(len(blocks), 2)
        self.assertEqual(blocks[0][0], ["Metric", "Q1", "Q2", "Q3"])
        self.assertEqual(blocks[0][1], ["Revenue", "10", "12", "15"])
        self.assertEqual(blocks[1][0], ["Region", "Users", "Growth"])
        self.assertEqual(blocks[1][1], ["CN", "1200", "12%"])

    def test_extract_table_blocks_handles_inconsistent_row_width(self) -> None:
        lines = [
            "Item | Value | Note",
            "A | 1",
            "B | 2 | ok",
            "C | 3 | stable",
            "end",
        ]

        blocks = _extract_table_blocks(lines)

        self.assertEqual(len(blocks), 1)
        # Most common width is 3, rows are padded/truncated to align.
        self.assertEqual(blocks[0][0], ["Item", "Value", "Note"])
        self.assertEqual(blocks[0][1], ["A", "1", ""])
        self.assertEqual(blocks[0][2], ["B", "2", "ok"])


if __name__ == '__main__':
    unittest.main()

