from __future__ import annotations

from copy import deepcopy
import unittest

from app.services.self_correct_service import analyze_quality_signals, apply_fix_ops_to_mapped_slide_plan, run_self_correct


class SelfCorrectServiceTestCase(unittest.TestCase):
    def test_analyze_quality_signals_includes_additional_quality_flags(self) -> None:
        slide_plan = [
            {
                'title': 'Executive Summary',
                'bullets': ['alpha', 'beta', 'gamma', 'delta', 'epsilon', 'zeta'],
                'visuals': ['img-1'],
            },
            {
                'title': 'Ops',
                'bullets': ['one'],
                'visuals': [],
            },
            {
                'title': '',
                'bullets': [],
                'visuals': ['img-2', 'img-3', 'img-4'],
            },
        ]
        edit_ops = [
            {'op': 'align_and_space'},
            {'op': 'move_title'},
            {'op': 'resize_panel'},
            {'op': 'align_margins'},
            {'op': 'position_element'},
            {'op': 'layout_reflow'},
        ]

        result = analyze_quality_signals(slide_plan=slide_plan, edit_ops=edit_ops, detail_level='concise')
        scores = result['signals']['scores']
        flags = result['signals']['flags']

        self.assertIn('alignment_risk', result)
        self.assertIn('density_imbalance', result)
        self.assertIn('title_consistency', result)
        self.assertIn('alignment_risk', scores)
        self.assertIn('density_imbalance', scores)
        self.assertIn('title_consistency', scores)
        self.assertTrue(flags['alignment_risk'])
        self.assertTrue(flags['density_imbalance'])
        self.assertTrue(flags['title_consistency'])

    def test_run_self_correct_exposes_new_flags_in_quality_report(self) -> None:
        slide_plan = [
            {
                'title': 'Alpha',
                'bullets': ['one', 'two', 'three', 'four', 'five', 'six'],
                'visuals': ['img-1'],
            },
            {
                'title': 'An exceptionally long section title for title consistency checks',
                'bullets': ['one'],
                'visuals': [],
            },
            {
                'title': '',
                'bullets': [],
                'visuals': ['img-2', 'img-3'],
            },
        ]
        edit_ops = [
            {'op': 'align_left'},
            {'op': 'move_title_block'},
            {'op': 'resize_panel'},
            {'op': 'align_right'},
            {'op': 'position_block'},
        ]

        result = run_self_correct(slide_plan=slide_plan, edit_ops=edit_ops, detail_level='balanced')
        quality_report = result['quality_report']

        self.assertIn('alignment_risk', quality_report)
        self.assertIn('density_imbalance', quality_report)
        self.assertIn('title_consistency', quality_report)
        self.assertIn('signals', quality_report)
        self.assertIn('flags', quality_report['signals'])
        self.assertTrue(quality_report['signals']['flags']['alignment_risk'])
        self.assertTrue(quality_report['signals']['flags']['density_imbalance'])
        self.assertTrue(quality_report['signals']['flags']['title_consistency'])

    def test_apply_fix_ops_to_mapped_slide_plan_applies_main_slide_ops(self) -> None:
        mapped_slide_plan = [
            {
                'page_no': 1,
                'title': '   A very long title that should be standardized and trimmed for the slide   ',
                'bullets': ['one', 'two', 'three', 'four', 'five', 'six'],
            },
            {
                'page_no': 2,
                'title': 'Summary',
                'bullets': [],
            },
        ]
        original_plan = deepcopy(mapped_slide_plan)
        fix_ops = [
            {'op': 'reduce_text_density', 'max_bullets': 3},
            {'op': 'shrink_typography', 'font_scale': 0.88},
            {'op': 'reflow_layout', 'target': 'content_slides', 'action': 'increase_spacing'},
            {'op': 'fill_empty_space'},
            {'op': 'standardize_titles'},
        ]

        result = apply_fix_ops_to_mapped_slide_plan(mapped_slide_plan, fix_ops, detail_level='balanced')
        plan = result['mapped_slide_plan']
        deck_metadata = result['deck_metadata']

        self.assertEqual(mapped_slide_plan, original_plan)
        self.assertEqual(plan[0]['bullets'], ['one', 'two', 'three'])
        self.assertEqual(plan[0]['font_scale'], 0.88)
        self.assertEqual(plan[0]['title'], 'A very long title that should be standardized...')
        self.assertEqual(plan[0]['layout_adjustments'][0]['op'], 'reflow_layout')
        self.assertEqual(plan[1]['bullets'], ['Summary: Summary'])
        self.assertTrue(plan[0]['slide_metadata']['text_density_reduced'])
        self.assertTrue(plan[1]['slide_metadata']['empty_space_filled'])
        self.assertTrue(plan[0]['slide_metadata']['title_standardized'])
        self.assertEqual(deck_metadata['detail_level'], 'balanced')
        self.assertEqual(deck_metadata['rebalance_ops'], [])
        self.assertIsNone(deck_metadata['rebalance_mode'])

    def test_apply_fix_ops_to_mapped_slide_plan_records_deck_rebalance_metadata(self) -> None:
        mapped_slide_plan = [
            {'page_no': 1, 'title': 'Alpha', 'bullets': ['one']},
            {'page_no': 2, 'title': 'Beta', 'bullets': ['two']},
        ]
        fix_ops = [
            {'op': 'global_rebalance', 'priority': 0, 'reason': 'high_risk_balanced'},
            {'op': 'selective_rebalance', 'priority': 1, 'reason': 'moderate_risk_balanced'},
        ]

        result = apply_fix_ops_to_mapped_slide_plan(mapped_slide_plan, fix_ops)
        deck_metadata = result['deck_metadata']

        self.assertEqual(deck_metadata['rebalance_mode'], 'global')
        self.assertEqual([item['op'] for item in deck_metadata['rebalance_ops']], ['global_rebalance', 'selective_rebalance'])
        self.assertEqual(result['mapped_slide_plan'][0]['title'], 'Alpha')
        self.assertEqual(result['mapped_slide_plan'][1]['title'], 'Beta')

    def test_apply_fix_ops_to_mapped_slide_plan_noop_keeps_plan_unchanged(self) -> None:
        mapped_slide_plan = [{'page_no': 1, 'title': 'Keep me', 'bullets': ['a', 'b']}]
        original_plan = deepcopy(mapped_slide_plan)

        result = apply_fix_ops_to_mapped_slide_plan(mapped_slide_plan, [{'op': 'noop'}])

        self.assertEqual(mapped_slide_plan, original_plan)
        self.assertEqual(result['mapped_slide_plan'], original_plan)
        self.assertEqual(result['applied_fix_ops'][0]['op'], 'noop')
        self.assertEqual(result['deck_metadata']['rebalance_ops'], [])
        self.assertIsNone(result['deck_metadata']['rebalance_mode'])


if __name__ == '__main__':
    unittest.main()
