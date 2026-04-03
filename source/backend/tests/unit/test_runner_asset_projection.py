from __future__ import annotations

import os
import unittest

os.environ.setdefault('DATABASE_URL', 'sqlite+pysqlite:///:memory:')

from app.workers.runner import (
    _build_image_asset,
    _build_slot_fill_value,
    _build_table_asset,
    _determine_slot_fill_status,
    _inject_slide_assets,
)


class RunnerAssetProjectionTestCase(unittest.TestCase):
    def test_build_slot_fill_value_prefers_parsed_image_asset(self) -> None:
        slide = {'page_no': 2, 'title': 'Market Overview', 'bullets': ['A', 'B']}
        slot = {'slot_type': 'image', 'slot_role': 'figure'}
        cursor_state = {'image_index': 0}
        parsed_images = [
            {'page_no': 2, 'image_path': '/tmp/p2_1.png', 'caption': 'P2 image', 'alt_text': 'P2 image alt'},
            {'page_no': 3, 'image_path': '/tmp/p3_1.png', 'caption': 'P3 image', 'alt_text': 'P3 image alt'},
        ]

        planned = _build_slot_fill_value(
            slide,
            slot,
            parsed_images=parsed_images,
            parsed_tables=[],
            asset_cursor_state=cursor_state,
        )

        self.assertEqual(planned['source'], 'doc_image')
        self.assertEqual(planned['hint']['image_path'], '/tmp/p2_1.png')
        self.assertEqual(planned['hint']['caption'], 'P2 image')
        self.assertEqual(cursor_state['image_index'], 1)

    def test_build_slot_fill_value_prefers_parsed_table_asset(self) -> None:
        slide = {'page_no': 3, 'title': 'Revenue', 'bullets': ['A', 'B']}
        slot = {'slot_type': 'table', 'slot_role': 'datatable'}
        cursor_state = {'table_index': 0}
        parsed_tables = [
            {'page_no': 3, 'title': 'Revenue Table', 'headers': ['Year', 'Value'], 'rows': [['2024', '10']]},
        ]

        planned = _build_slot_fill_value(
            slide,
            slot,
            parsed_images=[],
            parsed_tables=parsed_tables,
            asset_cursor_state=cursor_state,
        )

        self.assertEqual(planned['source'], 'doc_table')
        self.assertEqual(planned['hint']['title'], 'Revenue Table')
        self.assertEqual(planned['hint']['headers'], ['Year', 'Value'])
        self.assertEqual(planned['hint']['rows'], [['2024', '10']])
        self.assertEqual(cursor_state['table_index'], 1)

    def test_determine_slot_fill_status_marks_asset_backed_image_table_as_success(self) -> None:
        image_status = _determine_slot_fill_status(
            slot_type='image',
            planned_value={'source': 'doc_image', 'hint': {'image_path': '/tmp/p1.png', 'caption': 'cap'}},
            template_mapping_fallback_level=0,
        )
        table_status = _determine_slot_fill_status(
            slot_type='table',
            planned_value={'source': 'doc_table', 'hint': {'headers': ['h1'], 'rows': [['v1']]}},
            template_mapping_fallback_level=0,
        )

        self.assertEqual(image_status[0], 'success')
        self.assertEqual(table_status[0], 'success')
        self.assertGreaterEqual(image_status[1], 0.9)
        self.assertGreaterEqual(table_status[1], 0.9)

    def test_determine_slot_fill_status_marks_fallback_assets(self) -> None:
        image_status = _determine_slot_fill_status(
            slot_type='image',
            planned_value={'source': 'doc_image', 'hint': 'image_for_slide_1'},
            template_mapping_fallback_level=0,
        )
        table_status = _determine_slot_fill_status(
            slot_type='table',
            planned_value={'source': 'doc_table', 'hint': ['A', 'B']},
            template_mapping_fallback_level=0,
        )

        self.assertEqual(image_status[0], 'fallback')
        self.assertEqual(table_status[0], 'fallback')

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
