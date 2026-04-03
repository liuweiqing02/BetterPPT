from __future__ import annotations

import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault('DATABASE_URL', 'sqlite+pysqlite:///:memory:')

from app.core.constants import TaskStepCode
from app.workers.runner import (
    _apply_fix_ops_to_mapped_slide_plan,
    _build_self_correct_step_output,
    _normalize_quality_payload,
    _resolve_export_slide_plan,
)


class RunnerQualityPayloadTestCase(unittest.TestCase):
    def test_normalize_quality_payload_uses_plan_metrics_and_metric_v10(self) -> None:
        mapped_slide_plan = [
            {
                'page_no': 1,
                'page_function': 'cover',
                'title': 'Cover',
                'bullets': ['Intro'],
            },
            {
                'page_no': 2,
                'page_function': 'content',
                'title': 'Editable slide',
                'bullets': ['A', 'B'],
            },
            {
                'page_no': 3,
                'page_function': 'content',
                'title': '',
                'bullets': [],
            },
        ]
        slot_fill_plan = [
            {
                'slide_no': 2,
                'slot_type': 'text',
                'fill_status': 'success',
            },
            {
                'slide_no': 2,
                'slot_type': 'image',
                'fill_status': 'success',
            },
            {
                'slide_no': 3,
                'slot_type': 'image',
                'fill_status': 'fallback',
            },
            {
                'slide_no': 3,
                'slot_type': 'table',
                'fill_status': 'fallback',
            },
        ]
        task = SimpleNamespace(page_count_final=None, page_count_estimated=None, detail_level='balanced')
        step_outputs = {
            TaskStepCode.MAP_SLOTS: {
                'mapped_slide_plan': mapped_slide_plan,
                'slot_fill_plan': slot_fill_plan,
            }
        }
        self_correct_output = {
            'quality_report': {
                'risk_score': 0.25,
                'overflow': False,
                'collision': False,
            },
            'fix_ops': [],
            'mapped_slide_plan': mapped_slide_plan,
            'slot_fill_plan': slot_fill_plan,
        }

        payload = _normalize_quality_payload(
            task,
            step_outputs=step_outputs,
            self_correct_output=self_correct_output,
            attempt_no=1,
        )

        self.assertEqual(payload['metric_version'], 'v1.0')
        self.assertEqual(payload['evaluated_pages'], 2)
        self.assertAlmostEqual(payload['editable_text_ratio'], 0.5, places=4)
        self.assertAlmostEqual(payload['locked_page_ratio'], 0.5, places=4)
        self.assertEqual(payload['report_json']['page_metrics']['evaluated_pages'], 2)
        self.assertEqual(payload['evaluated_scope_json']['excluded_page_types'], ['cover', 'toc'])

    def test_self_correct_applies_fix_ops_and_export_prefers_corrected_plan(self) -> None:
        mapped_slide_plan = [
            {
                'page_no': 1,
                'page_function': 'content',
                'title': '  very    long   title  ',
                'bullets': ['One', 'Two', 'Three', 'Four', 'Five'],
                'layout_schema_json': {},
                'style_tokens_json': {},
            },
            {
                'page_no': 2,
                'page_function': 'content',
                'title': 'Second slide',
                'bullets': ['Keep'],
                'layout_schema_json': {},
                'style_tokens_json': {},
            },
        ]
        step_outputs = {
            TaskStepCode.PLAN_SLIDES: {
                'slide_plan': mapped_slide_plan,
            },
            TaskStepCode.MAP_SLOTS: {
                'mapped_slide_plan': mapped_slide_plan,
                'slot_fill_plan': [],
            },
            TaskStepCode.GENERATE_SLIDES: {
                'mapped_slide_plan': mapped_slide_plan,
                'edit_ops': [{'op': 'replace_title', 'value': 'placeholder'}],
            },
        }
        task = SimpleNamespace(detail_level='balanced')

        with patch('app.workers.runner.run_self_correct') as mock_run_self_correct:
            mock_run_self_correct.return_value = {
                'fix_ops': [
                    {'op': 'reduce_text_density', 'priority': 1},
                    {'op': 'standardize_titles', 'priority': 2},
                ],
                'quality_report': {
                    'risk_score': 0.8,
                    'overflow': True,
                    'collision': False,
                },
            }

            output = _build_self_correct_step_output(task, step_outputs)

        corrected_plan = output['mapped_slide_plan']
        self.assertEqual(corrected_plan[0]['bullets'], ['One', 'Two', 'Three'])
        self.assertEqual(corrected_plan[0]['title'], 'very long title')
        self.assertEqual(corrected_plan[0]['layout_schema_json']['layout_rules']['density_hint'], 'compact')
        self.assertEqual(output['applied_fix_ops'][0]['op'], 'reduce_text_density')

        export_plan = _resolve_export_slide_plan(
            {
                TaskStepCode.PLAN_SLIDES: step_outputs[TaskStepCode.PLAN_SLIDES],
                TaskStepCode.MAP_SLOTS: step_outputs[TaskStepCode.MAP_SLOTS],
                TaskStepCode.GENERATE_SLIDES: step_outputs[TaskStepCode.GENERATE_SLIDES],
                TaskStepCode.SELF_CORRECT: output,
            }
        )
        self.assertEqual(export_plan[0]['bullets'], ['One', 'Two', 'Three'])
        self.assertEqual(export_plan[0]['title'], 'very long title')

    def test_apply_fix_ops_handles_font_reduce_alias(self) -> None:
        mapped_slide_plan = [
            {
                'page_no': 1,
                'page_function': 'content',
                'title': 'A title',
                'bullets': ['A', 'B', 'C', 'D'],
                'layout_schema_json': {'layout_rules': {}},
                'style_tokens_json': {'font_size_scale': 1.0},
            }
        ]

        corrected = _apply_fix_ops_to_mapped_slide_plan(
            mapped_slide_plan,
            [{'op': 'font_reduce', 'priority': 1}],
        )

        self.assertEqual(corrected[0]['bullets'], ['A', 'B', 'C'])
        self.assertEqual(corrected[0]['layout_schema_json']['layout_rules']['density_hint'], 'compact')
        self.assertLess(corrected[0]['style_tokens_json']['font_size_scale'], 1.0)


if __name__ == '__main__':
    unittest.main()
