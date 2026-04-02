from __future__ import annotations

import unittest

from app.workers.runner import _build_image_asset, _build_table_asset, _inject_slide_assets


class RunnerAssetProjectionTestCase(unittest.TestCase):
    def test_build_table_asset_from_list_of_dicts_infers_headers_and_rows(self) -> None:
        asset = _build_table_asset(
            {
                'slot_key': 'summary_table',
                'slot_type': 'table',
                'fill_json': {
                    'planned_value': {
                        'hint': [
                            {'name': 'Users', 'value': '128', 'growth': '12%'},
                            {'name': 'Conversions', 'value': '4.2%', 'growth': '0.7%'},
                        ]
                    }
                },
            }
        )

        self.assertEqual(asset['headers'], ['name', 'value', 'growth'])
        self.assertEqual(
            asset['rows'],
            [
                ['Users', '128', '12%'],
                ['Conversions', '4.2%', '0.7%'],
            ],
        )

    def test_build_table_asset_from_dict_preserves_headers_and_rows(self) -> None:
        asset = _build_table_asset(
            {
                'slot_key': 'metrics_table',
                'slot_type': 'table',
                'content_source': 'doc_table',
                'fill_json': {
                    'planned_value': {
                        'hint': {
                            'headers': ['Metric', 'Value'],
                            'rows': [
                                ['Users', '128'],
                                ['Conversion', '4.2%'],
                            ],
                        }
                    }
                },
            }
        )

        self.assertEqual(asset['headers'], ['Metric', 'Value'])
        self.assertEqual(asset['rows'], [['Users', '128'], ['Conversion', '4.2%']])

    def test_build_image_asset_transfers_dict_hint_fields(self) -> None:
        asset = _build_image_asset(
            {
                'slot_key': 'hero_image',
                'slot_type': 'image',
                'content_source': 'doc_image',
                'fill_json': {
                    'planned_value': {
                        'hint': {
                            'path': '/tmp/hero.png',
                            'caption': 'Hero banner',
                            'alt_text': 'Hero banner alt',
                            'source': 'reference_asset',
                        }
                    }
                },
            }
        )

        self.assertEqual(asset['image_path'], '/tmp/hero.png')
        self.assertEqual(asset['caption'], 'Hero banner')
        self.assertEqual(asset['alt_text'], 'Hero banner alt')
        self.assertEqual(asset['source'], 'reference_asset')

    def test_inject_slide_assets_groups_tables_and_images_and_skips_empty_items(self) -> None:
        mapped_slide_plan = [
            {'page_no': 1, 'title': 'Cover'},
            {'page_no': 2, 'title': 'Details'},
        ]
        slot_fill_plan = [
            {
                'slide_no': 1,
                'slot_key': 'summary_table',
                'slot_type': 'table',
                'fill_json': {
                    'planned_value': {
                        'hint': [
                            {'name': 'Users', 'value': '128'},
                            {'name': 'Conversion', 'value': '4.2%'},
                        ]
                    }
                },
            },
            {
                'slide_no': 2,
                'slot_key': 'hero_image',
                'slot_type': 'image',
                'fill_json': {
                    'planned_value': {
                        'hint': {
                            'path': '/tmp/hero.png',
                            'caption': 'Hero banner',
                        }
                    }
                },
            },
            {
                'slide_no': 2,
                'slot_key': 'empty_table',
                'slot_type': 'table',
                'fill_json': {'planned_value': {}},
            },
            {
                'slide_no': 2,
                'slot_key': 'empty_image',
                'slot_type': 'image',
                'fill_json': {'planned_value': {'hint': {'path': '', 'caption': ''}}},
            },
        ]

        enriched = _inject_slide_assets(mapped_slide_plan, slot_fill_plan)

        self.assertEqual(len(enriched), 2)
        self.assertEqual(enriched[0]['tables'][0]['headers'], ['name', 'value'])
        self.assertEqual(enriched[0]['tables'][0]['rows'], [['Users', '128'], ['Conversion', '4.2%']])
        self.assertEqual(enriched[1]['images'][0]['image_path'], '/tmp/hero.png')
        self.assertEqual(enriched[1]['images'][0]['caption'], 'Hero banner')
        self.assertNotIn('tables', enriched[1])
        self.assertNotIn('images', enriched[0])


if __name__ == '__main__':
    unittest.main()
