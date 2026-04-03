from __future__ import annotations

import os
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault('DATABASE_URL', 'sqlite+pysqlite:///:memory:')

from app.core.constants import TaskStepCode
from app.workers.runner import (
    _apply_text_overflow_strategy,
    _build_parse_pdf_step_output,
    _build_generate_slides_step_output,
    _build_map_slots_step_output,
    _build_rag_step_output,
    _build_self_correct_step_output,
)


def _llm_result(payload: dict[str, object]) -> SimpleNamespace:
    return SimpleNamespace(
        content=json.dumps(payload, ensure_ascii=False),
        usage={'prompt_tokens': 12, 'completion_tokens': 24},
        raw={'choices': [{'message': {'content': json.dumps(payload, ensure_ascii=False)}}]},
    )


class RunnerLlmAgentsTestCase(unittest.TestCase):
    def test_text_overflow_strategy_split_page_first(self) -> None:
        mapped = [
            {
                'page_no': 1,
                'title': 'Long Content',
                'bullets': [f'Point {idx} details' for idx in range(1, 13)],
                'tables': [{'title': 'T1'}],
                'images': [{'caption': 'I1'}],
            }
        ]
        result, stats = _apply_text_overflow_strategy(mapped, 'concise')
        self.assertGreaterEqual(stats['split_pages'], 1)
        self.assertEqual(result[0]['title'], 'Long Content')
        self.assertTrue(result[1]['title'].startswith('Long Content (Continued'))
        self.assertEqual(result[1].get('tables'), [])
        self.assertEqual(result[1].get('images'), [])
        self.assertEqual(result[0]['page_no'], 1)
        self.assertEqual(result[1]['page_no'], 2)

    def test_parse_pdf_refines_with_llm_when_available(self) -> None:
        with tempfile.TemporaryDirectory(prefix='runner-parse-llm-') as tempdir:
            root = Path(tempdir)
            source_path = root / 'source.pdf'
            source_path.write_bytes(b'%PDF-1.4 mock')
            task = SimpleNamespace(task_no='TASK-LLM-PARSE', detail_level='balanced', user_prompt='focus outcomes', user_id=7)
            source_file = SimpleNamespace(
                id=11,
                filename='source.pdf',
                ext='pdf',
                file_size=source_path.stat().st_size,
                storage_path='source.pdf',
            )
            settings = SimpleNamespace(storage_root_path=root, result_subdir='results')
            parse_payload = {
                'sections': [{'title': 'Intro', 'page': 1}],
                'key_facts': ['baseline fact'],
                'evidence_spans': [{'page': 1, 'text': 'baseline evidence'}],
                'images': [],
                'tables': [],
            }
            llm_payload = {
                'sections': [{'title': 'Executive Summary', 'page': 1}],
                'key_facts': ['Fact A', 'Fact B'],
                'evidence_spans': [{'page': 2, 'text': 'Evidence from page 2'}],
                'doc_summary': 'Summary by llm',
            }

            with patch('app.workers.runner.get_settings', return_value=settings), patch(
                'app.workers.runner.parse_pdf_document', return_value=parse_payload
            ), patch('app.workers.runner.call_chat_completions', return_value=_llm_result(llm_payload)):
                output, fallback_used = _build_parse_pdf_step_output(task, source_file)

        self.assertFalse(fallback_used)
        self.assertEqual(output['analysis_source'], 'parse_pdf_llm')
        self.assertTrue(output['llm_used'])
        self.assertFalse(output['fallback_used'])
        self.assertIsNone(output['fallback_reason'])
        self.assertEqual(output['sections'][0]['title'], 'Executive Summary')
        self.assertEqual(output['key_facts'][0], 'Fact A')
        self.assertEqual(output['evidence_spans'][0]['page'], 2)
        self.assertEqual(output['doc_summary'], 'Summary by llm')
        self.assertEqual(output['llm_usage']['prompt_tokens'], 12)

    def test_rag_retrieve_uses_llm_query_when_available(self) -> None:
        task = SimpleNamespace(
            task_no='TASK-LLM-RAG',
            detail_level='balanced',
            rag_enabled=True,
            user_prompt='focus market sizing',
        )
        source_file = SimpleNamespace(id=22, filename='source.pdf')
        llm_payload = {
            'query': 'market sizing growth risk',
            'topic_weights': {'market': 0.9, 'risk': 0.6},
        }

        with patch(
            'app.workers.runner._read_source_text',
            return_value=('market analysis with risk factors', False, {'source_text_chars': 33, 'truncated': False, 'binary_like': False}),
        ), patch('app.workers.runner.call_chat_completions', return_value=_llm_result(llm_payload)), patch(
            'app.workers.runner.chunk_document_text', return_value=[{'chunk_id': 'c1', 'text': 'chunk'}]
        ), patch(
            'app.workers.runner.retrieve_chunks',
            return_value={'retrieved_chunks': [{'chunk_id': 'c1', 'score': 0.99}], 'citations': [{'source_page': 1}]},
        ):
            output, fallback_used = _build_rag_step_output(task, source_file)

        self.assertFalse(fallback_used)
        self.assertEqual(output['analysis_source'], 'rag_llm')
        self.assertTrue(output['llm_used'])
        self.assertFalse(output['fallback_used'])
        self.assertIsNone(output['fallback_reason'])
        self.assertEqual(output['query_source'], 'llm')
        self.assertEqual(output['query'], 'market sizing growth risk')
        self.assertEqual(output['topic_weights']['market'], 0.9)
        self.assertEqual(output['llm_usage']['completion_tokens'], 24)

    def test_rag_retrieve_falls_back_to_rule_query_on_llm_error(self) -> None:
        task = SimpleNamespace(
            task_no='TASK-LLM-RAG-FB',
            detail_level='balanced',
            rag_enabled=True,
            user_prompt='focus profitability',
        )
        source_file = SimpleNamespace(id=23, filename='source.pdf')

        with patch(
            'app.workers.runner._read_source_text',
            return_value=('profitability margin trend', False, {'source_text_chars': 27, 'truncated': False, 'binary_like': False}),
        ), patch('app.workers.runner.call_chat_completions', side_effect=RuntimeError('llm down')), patch(
            'app.workers.runner.chunk_document_text', return_value=[{'chunk_id': 'c1', 'text': 'chunk'}]
        ), patch(
            'app.workers.runner.retrieve_chunks',
            return_value={'retrieved_chunks': [{'chunk_id': 'c1', 'score': 0.77}], 'citations': [{'source_page': 1}]},
        ):
            output, fallback_used = _build_rag_step_output(task, source_file)

        self.assertFalse(fallback_used)
        self.assertEqual(output['analysis_source'], 'rag_service')
        self.assertFalse(output['llm_used'])
        self.assertTrue(output['fallback_used'])
        self.assertEqual(output['fallback_reason'], 'llm down')
        self.assertEqual(output['query_source'], 'rule')
        self.assertIn('profitability', output['query'])
        self.assertIsNone(output['llm_usage'])

    def test_map_slots_uses_llm_overrides_when_available(self) -> None:
        task = SimpleNamespace(
            id=1,
            task_no='TASK-LLM-MAP',
            detail_level='balanced',
            rag_enabled=False,
            user_prompt='',
            page_count_estimated=1,
            fallback_used=0,
        )
        db = SimpleNamespace(execute=lambda *args, **kwargs: None, add=lambda *args, **kwargs: None, flush=lambda: None)
        step_outputs = {
            TaskStepCode.PLAN_SLIDES: {
                'slide_plan': [
                    {
                        'page_no': 1,
                        'title': 'Overview',
                        'bullets': ['A', 'B'],
                    }
                ]
            },
            TaskStepCode.ASSETIZE_TEMPLATE: {
                'asset_pages': [
                    {
                        'page_no': 1,
                        'cluster_label': 'cluster_01',
                        'page_function': 'content',
                        'layout_schema_json': {
                            'slots': [
                                {'slot_key': 'title', 'slot_type': 'text', 'slot_role': 'title'},
                                {'slot_key': 'hero_visual', 'slot_type': 'image', 'slot_role': 'figure'},
                            ]
                        },
                        'style_tokens_json': {},
                    }
                ]
            },
            TaskStepCode.PARSE_PDF: {'images': [], 'tables': []},
        }
        llm_payload = {
            'page_suggestions': [
                {
                    'page_no': 1,
                    'page_function': 'cover',
                    'layout_suggestions': {'density_hint': 'compact'},
                    'style_suggestions': {'accent_color': '#123456'},
                }
            ],
            'slot_fill_overrides': [
                {
                    'slide_no': 1,
                    'slot_key': 'hero_visual',
                    'slot_type': 'image',
                    'content_source': 'doc_image',
                    'fill_status': 'success',
                    'quality_score': 0.98,
                    'planned_value': {
                        'hint': {
                            'image_path': 'C:/tmp/hero.png',
                            'caption': 'Hero image',
                            'alt_text': 'Hero image',
                            'source': 'llm',
                        }
                    },
                }
            ],
        }

        with patch('app.workers.runner.call_chat_completions', return_value=_llm_result(llm_payload)):
            output = _build_map_slots_step_output(
                db,
                task,
                step_outputs=step_outputs,
                template_profile_id=None,
                attempt_no=1,
            )

        self.assertEqual(output['analysis_source'], 'map_slots_llm')
        self.assertEqual(output['llm_usage']['prompt_tokens'], 12)
        self.assertEqual(output['llm_suggestions_total'], 1)
        self.assertEqual(output['llm_suggestions_applied'], 1)
        self.assertEqual(output['mapped_slide_plan'][0]['page_function'], 'cover')
        self.assertEqual(output['mapped_slide_plan'][0]['layout_schema_json']['layout_rules']['density_hint'], 'compact')
        hero_slot = next(item for item in output['slot_fill_plan'] if item['slot_key'] == 'hero_visual')
        self.assertEqual(hero_slot['content_source'], 'doc_image')
        self.assertEqual(hero_slot['fill_status'], 'success')
        self.assertEqual(hero_slot['fill_json']['planned_value']['hint']['image_path'], 'C:/tmp/hero.png')

    def test_generate_slides_prefers_llm_edit_ops(self) -> None:
        task = SimpleNamespace(
            id=1,
            task_no='TASK-LLM-GEN',
            detail_level='balanced',
            rag_enabled=False,
            user_prompt='',
            page_count_estimated=1,
            fallback_used=0,
        )
        db = SimpleNamespace()
        step_outputs = {
            TaskStepCode.PLAN_SLIDES: {
                'slide_plan': [
                    {
                        'page_no': 1,
                        'title': 'Overview',
                        'bullets': ['A', 'B'],
                    }
                ]
            },
            TaskStepCode.MAP_SLOTS: {
                'mapped_slide_plan': [
                    {
                        'page_no': 1,
                        'title': 'Overview',
                        'bullets': ['A', 'B'],
                        'page_function': 'content',
                        'cluster_label': 'cluster_default',
                        'layout_schema_json': {'slots': ['title', 'bullets']},
                        'style_tokens_json': {},
                        'tables': [],
                        'images': [],
                    }
                ],
                'slot_fill_plan': [],
                'mapping_mode': 'default',
                'template_page_count': 0,
            },
            TaskStepCode.ANALYZE_TEMPLATE: {'llm_page_suggestions': []},
        }
        llm_payload = {
            'mapped_slide_plan': [
                {
                    'page_no': 1,
                    'title': 'LLM Overview',
                    'bullets': ['First', 'Second'],
                    'page_function': 'content',
                }
            ],
            'edit_ops': [
                {'op': 'replace_title', 'page_no': 1, 'value': 'LLM Overview'},
                {'op': 'replace_bullets', 'page_no': 1, 'value': ['First', 'Second']},
            ],
        }

        with patch('app.workers.runner.call_chat_completions', return_value=_llm_result(llm_payload)):
            output = _build_generate_slides_step_output(
                db,
                task,
                step_outputs=step_outputs,
                template_profile_id=None,
            )

        self.assertFalse(output[1])
        payload = output[0]
        self.assertEqual(payload['generation_source'], 'llm')
        self.assertEqual(payload['llm_usage']['completion_tokens'], 24)
        self.assertTrue(payload['llm_used'])
        self.assertFalse(payload['fallback_used'])
        self.assertIsNone(payload['fallback_reason'])
        self.assertEqual(payload['mapped_slide_plan'][0]['title'], 'LLM Overview')
        self.assertEqual(payload['edit_ops'][0]['op'], 'replace_title')
        self.assertEqual(payload['edit_ops'][0]['value'], 'LLM Overview')

    def test_generate_slides_reports_text_overflow_strategy(self) -> None:
        task = SimpleNamespace(
            id=1,
            task_no='TASK-LLM-GEN-SPLIT',
            detail_level='concise',
            rag_enabled=False,
            user_prompt='',
            page_count_estimated=1,
            fallback_used=0,
        )
        db = SimpleNamespace()
        step_outputs = {
            TaskStepCode.PLAN_SLIDES: {
                'slide_plan': [
                    {
                        'page_no': 1,
                        'title': 'Overflow Slide',
                        'bullets': [f'Point {idx} long description' for idx in range(1, 12)],
                    }
                ]
            },
            TaskStepCode.MAP_SLOTS: {
                'mapped_slide_plan': [
                    {
                        'page_no': 1,
                        'title': 'Overflow Slide',
                        'bullets': [f'Point {idx} long description' for idx in range(1, 12)],
                        'page_function': 'content',
                        'cluster_label': 'cluster_default',
                        'layout_schema_json': {'slots': ['title', 'bullets']},
                        'style_tokens_json': {},
                        'tables': [{'title': 't'}],
                        'images': [{'caption': 'i'}],
                    }
                ],
                'slot_fill_plan': [],
                'mapping_mode': 'default',
                'template_page_count': 0,
            },
            TaskStepCode.ANALYZE_TEMPLATE: {'llm_page_suggestions': []},
        }
        llm_payload = {'edit_ops': [{'op': 'replace_title', 'page_no': 1, 'value': 'LLM Title'}]}
        with patch('app.workers.runner.call_chat_completions', return_value=_llm_result(llm_payload)):
            output, _ = _build_generate_slides_step_output(
                db,
                task,
                step_outputs=step_outputs,
                template_profile_id=None,
            )
        self.assertIn('text_overflow_strategy', output)
        self.assertGreaterEqual(output['text_overflow_strategy']['split_pages'], 1)
        self.assertGreaterEqual(output['page_count'], 2)

    def test_self_correct_uses_llm_and_falls_back_when_llm_fails(self) -> None:
        task = SimpleNamespace(detail_level='balanced', task_no='TASK-LLM-SELF')
        step_outputs = {
            TaskStepCode.PLAN_SLIDES: {
                'slide_plan': [
                    {
                        'page_no': 1,
                        'title': 'A very long title that needs trimming',
                        'bullets': ['One', 'Two', 'Three', 'Four', 'Five'],
                    }
                ]
            },
            TaskStepCode.MAP_SLOTS: {
                'mapped_slide_plan': [
                    {
                        'page_no': 1,
                        'title': 'A very long title that needs trimming',
                        'bullets': ['One', 'Two', 'Three', 'Four', 'Five'],
                        'layout_schema_json': {'layout_rules': {}},
                        'style_tokens_json': {},
                    }
                ],
                'slot_fill_plan': [],
            },
            TaskStepCode.GENERATE_SLIDES: {
                'mapped_slide_plan': [
                    {
                        'page_no': 1,
                        'title': 'A very long title that needs trimming',
                        'bullets': ['One', 'Two', 'Three', 'Four', 'Five'],
                        'layout_schema_json': {'layout_rules': {}},
                        'style_tokens_json': {},
                    }
                ],
                'slot_fill_plan': [],
                'edit_ops': [{'op': 'replace_title', 'value': 'placeholder'}],
            },
        }
        llm_payload = {
            'fix_ops': [{'op': 'reduce_text_density', 'priority': 1}],
            'retry_recommended': True,
            'quality_report': {'risk_score': 0.15, 'overflow': False, 'collision': False},
            'reason_code': 'LLM_OK',
        }

        with patch('app.workers.runner.run_self_correct') as mock_rule_based:
            mock_rule_based.return_value = {
                'fix_ops': [{'op': 'font_reduce', 'priority': 1}],
                'quality_report': {'risk_score': 0.6, 'overflow': True, 'collision': False},
            }
            with patch('app.workers.runner.call_chat_completions', return_value=_llm_result(llm_payload)):
                output = _build_self_correct_step_output(task, step_outputs)

        self.assertEqual(output['analysis_source'], 'self_correct_llm')
        self.assertTrue(output['llm_used'])
        self.assertFalse(output['fallback_used'])
        self.assertIsNone(output['fallback_reason'])
        self.assertEqual(output['fix_ops'][0]['op'], 'reduce_text_density')
        self.assertTrue(output['retry_recommended'])
        self.assertEqual(output['reason_code'], 'LLM_OK')
        self.assertEqual(output['quality_report']['risk_score'], 0.15)
        self.assertEqual(output['mapped_slide_plan'][0]['bullets'], ['One', 'Two', 'Three'])

    def test_self_correct_falls_back_when_llm_errors(self) -> None:
        task = SimpleNamespace(detail_level='balanced', task_no='TASK-LLM-SELF-FB')
        step_outputs = {
            TaskStepCode.PLAN_SLIDES: {'slide_plan': [{'page_no': 1, 'title': 'Slide', 'bullets': ['A']}]},
            TaskStepCode.MAP_SLOTS: {
                'mapped_slide_plan': [{'page_no': 1, 'title': 'Slide', 'bullets': ['A'], 'layout_schema_json': {}, 'style_tokens_json': {}}],
                'slot_fill_plan': [],
            },
            TaskStepCode.GENERATE_SLIDES: {'mapped_slide_plan': [], 'slot_fill_plan': [], 'edit_ops': []},
        }

        with patch('app.workers.runner.run_self_correct') as mock_rule_based:
            mock_rule_based.return_value = {
                'fix_ops': [{'op': 'font_reduce', 'priority': 1}],
                'quality_report': {'risk_score': 0.6, 'overflow': False, 'collision': False},
            }
            with patch('app.workers.runner.call_chat_completions', side_effect=RuntimeError('llm offline')):
                output = _build_self_correct_step_output(task, step_outputs)

        self.assertEqual(output['analysis_source'], 'self_correct_service')
        self.assertFalse(output['llm_used'])
        self.assertTrue(output['fallback_used'])
        self.assertIn('llm offline', output['fallback_reason'])
        self.assertEqual(output['fix_ops'][0]['op'], 'font_reduce')


if __name__ == '__main__':
    unittest.main()
