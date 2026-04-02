from __future__ import annotations

import unittest

from app.services.layout_service import apply_template_llm_suggestions, map_slide_plan_to_template


class LayoutServiceTestCase(unittest.TestCase):
    def test_map_with_template_pages(self) -> None:
        slide_plan = [
            {'title': 'S1', 'bullets': ['a', 'b']},
            {'title': 'S2', 'bullets': ['c']},
            {'title': 'S3', 'bullets': ['d']},
        ]
        template_pages = [
            {
                'page_no': 1,
                'cluster_label': 'cluster_01',
                'page_function': 'cover',
                'layout_schema_json': {'slots': ['title', 'hero']},
                'style_tokens_json': {'accent_color': '#112233'},
            },
            {
                'page_no': 2,
                'cluster_label': 'cluster_02',
                'page_function': 'content',
                'layout_schema_json': {'slots': ['title', 'bullets']},
                'style_tokens_json': {'accent_color': '#334455'},
            },
        ]

        result = map_slide_plan_to_template(slide_plan=slide_plan, template_pages=template_pages)
        mapped = result['mapped_slide_plan']

        self.assertEqual(result['mapping_mode'], 'template')
        self.assertEqual(result['template_page_count'], 2)
        self.assertEqual(len(mapped), 3)
        self.assertEqual(mapped[0]['page_function'], 'cover')
        self.assertEqual(mapped[1]['page_function'], 'content')
        self.assertEqual(mapped[2]['page_function'], 'cover')
        self.assertEqual(mapped[2]['template_page_no'], 1)

    def test_map_without_template_pages(self) -> None:
        slide_plan = [{'title': 'Only', 'bullets': []}]
        result = map_slide_plan_to_template(slide_plan=slide_plan, template_pages=[])

        self.assertEqual(result['mapping_mode'], 'default')
        self.assertEqual(result['template_page_count'], 0)
        self.assertEqual(len(result['mapped_slide_plan']), 1)
        self.assertEqual(result['mapped_slide_plan'][0]['page_function'], 'content')

    def test_apply_template_llm_suggestions_overrides_mapping_hints(self) -> None:
        mapped_slide_plan = [
            {
                'page_no': 1,
                'template_page_no': 1,
                'page_function': 'content',
                'layout_schema_json': {'layout_rules': {'density_hint': 'balanced', 'title_style': 'standard'}},
                'style_tokens_json': {'accent_color': '#112233', 'text_color': '#111111'},
            },
            {
                'page_no': 2,
                'template_page_no': 2,
                'page_function': 'content',
                'layout_schema_json': {'layout_rules': {'density_hint': 'balanced'}},
                'style_tokens_json': {'accent_color': '#334455', 'text_color': '#222222'},
            },
        ]
        llm_page_suggestions = [
            {
                'page_no': 1,
                'page_function': 'cover',
                'layout_suggestions': {'density_hint': 'compact', 'title_style': 'hero'},
                'style_suggestions': {'accent_strategy': 'brand_strip', 'accent_color': '#AA5500'},
                'reason': 'cover should be compact',
            },
            {
                'page_no': 2,
                'layout_suggestions': {'max_bullets': 4},
            },
        ]

        result = apply_template_llm_suggestions(mapped_slide_plan, llm_page_suggestions)
        updated = result['mapped_slide_plan']

        self.assertEqual(result['suggestions_total'], 2)
        self.assertEqual(result['suggestions_applied'], 2)
        self.assertEqual(updated[0]['page_function'], 'cover')
        self.assertEqual(updated[0]['page_function_source'], 'llm_template_suggestion')
        self.assertEqual(updated[0]['layout_schema_json']['layout_rules']['density_hint'], 'compact')
        self.assertEqual(updated[0]['layout_schema_json']['layout_rules']['title_style'], 'hero')
        self.assertEqual(updated[0]['style_tokens_json']['accent_strategy'], 'brand_strip')
        self.assertEqual(updated[0]['style_tokens_json']['accent_color'], '#AA5500')
        self.assertEqual(updated[1]['layout_schema_json']['layout_rules']['max_bullets'], 4)
        self.assertIn('llm_enhancement_json', updated[0])


if __name__ == '__main__':
    unittest.main()
