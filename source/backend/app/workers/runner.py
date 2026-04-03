from __future__ import annotations

import argparse
import importlib
import json
import logging
import re
import time
from copy import deepcopy
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.constants import (
    DETAIL_LEVEL_PAGE_RANGE,
    STEP_PROGRESS_RANGE,
    FileRole,
    TaskEventType,
    TaskStatus,
    TaskStepCode,
    TaskStepStatus,
)
from app.db.session import SessionLocal
from app.models.file import File
from app.models.task import Task
from app.models.task_page_mapping import TaskPageMapping
from app.models.task_quality_report import TaskQualityReport
from app.models.task_slot_filling import TaskSlotFilling
from app.models.task_step import TaskStep
from app.models.template_page_schema import TemplatePageSchema
from app.models.template_profile import TemplateProfile
from app.models.template_slot_definition import TemplateSlotDefinition
from app.services.event_service import add_task_event
from app.services.layout_service import apply_template_llm_suggestions, map_slide_plan_to_template
from app.services.llm_service import call_chat_completions
from app.services.file_service import _default_retention_expire_at
from app.services.pdf_parse_service import parse_pdf_document
from app.services.pptx_service import PPTXGenerationError, generate_pptx_from_plan
from app.services.queue_service import (
    ack_stream_event,
    acquire_task_lock,
    cache_task_progress,
    claim_task_from_stream,
    push_task_event_cache,
    release_task_lock,
)
from app.services.rag_service import build_query, chunk_document_text, retrieve_chunks
from app.services.self_correct_service import run_self_correct

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s')

_RAG_SOURCE_CHAR_LIMIT = 300_000
_RAG_SOURCE_BYTE_LIMIT = 900_000
_RAG_MIN_TEXT_CHARS = 160
_RAG_BINARY_THRESHOLD = 0.62
_LLM_SOURCE_CHAR_LIMIT = 8_000

_STEP_ORDER = {
    TaskStepCode.VALIDATE_INPUT: 1,
    TaskStepCode.PARSE_PDF: 2,
    TaskStepCode.ANALYZE_TEMPLATE: 3,
    TaskStepCode.ASSETIZE_TEMPLATE: 4,
    TaskStepCode.RAG_RETRIEVE: 5,
    TaskStepCode.PLAN_SLIDES: 6,
    TaskStepCode.MAP_SLOTS: 7,
    TaskStepCode.GENERATE_SLIDES: 8,
    TaskStepCode.SELF_CORRECT: 9,
    TaskStepCode.EXPORT_PPT: 10,
}

_RETRYABLE_ATTEMPT_STEPS = {
    TaskStepCode.MAP_SLOTS,
    TaskStepCode.GENERATE_SLIDES,
    TaskStepCode.SELF_CORRECT,
    TaskStepCode.EXPORT_PPT,
}
_MAX_SELF_CORRECT_ATTEMPTS = 2


class SelfCorrectRetryRequired(Exception):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


def _default_step_input(task: Task, step_code: str) -> dict:
    return {
        'task_no': task.task_no,
        'detail_level': task.detail_level,
        'rag_enabled': bool(task.rag_enabled),
        'step_code': step_code,
    }


def _infer_step_audit_fields(step_code: str, output_json: dict[str, Any]) -> tuple[bool, bool, str | None]:
    analysis_source = str(output_json.get('analysis_source') or '').strip().lower()
    plan_source = str(output_json.get('plan_source') or '').strip().lower()
    generation_source = str(output_json.get('generation_source') or '').strip().lower()
    query_source = str(output_json.get('query_source') or '').strip().lower()
    llm_used = bool(output_json.get('llm_usage')) or bool(output_json.get('llm_enhanced'))
    llm_used = llm_used or analysis_source.endswith('_llm') or plan_source == 'llm' or generation_source == 'llm' or query_source == 'llm'
    fallback_reason = output_json.get('fallback_reason')

    fallback_used = bool(output_json.get('fallback_used'))
    fallback_used = fallback_used or analysis_source in {'parse_pdf_fallback', 'rag_fallback', 'self_correct_service'}
    fallback_used = fallback_used or analysis_source == 'runner_fallback'
    fallback_used = fallback_used or plan_source == 'mock_fallback'
    fallback_used = fallback_used or generation_source == 'rule_based_from_plan'
    fallback_used = fallback_used or (analysis_source == 'slot_mapping_service' and bool(output_json.get('llm_error')))
    fallback_used = fallback_used or (query_source == 'rule' and bool(output_json.get('llm_error')))
    fallback_used = fallback_used or str(output_json.get('parse_status') or '').strip().lower() in {'fallback', 'partial_fallback'}
    fallback_used = fallback_used or bool(output_json.get('analysis_warnings'))
    if fallback_used and fallback_reason is None and output_json.get('llm_error'):
        fallback_reason = output_json.get('llm_error')

    return llm_used, fallback_used, fallback_reason


def _attach_step_audit_fields(step_code: str, output_json: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(output_json, dict):
        return output_json

    llm_used, fallback_used, fallback_reason = _infer_step_audit_fields(step_code, output_json)
    output_json.setdefault('llm_used', llm_used)
    output_json.setdefault('fallback_used', fallback_used)
    if fallback_reason is not None and output_json.get('fallback_reason') is None:
        output_json['fallback_reason'] = fallback_reason
    output_json.setdefault('fallback_reason', None)
    return output_json


def _extract_keywords_from_text(text: str, limit: int = 10) -> list[str]:
    tokens = re.findall(r"[A-Za-z0-9]{2,}|[\u4e00-\u9fff]{2,}", (text or '').lower())
    if not tokens:
        return []

    stopwords = {
        'the', 'and', 'for', 'with', 'from', 'that', 'this', 'into', 'onto', 'about', 'through',
        '根据', '以及', '一个', '一些', '我们', '你们', '他们', '她们', '它们', '可以', '需要', '进行',
        '因此', '同时', '如果', '由于', '但是', '或者', '并且', '并', '或', '与', '及', '和', '在', '对',
        '的', '了', '是', '为', '于', '中', '上', '下', '而', '被', '将', '把', '及其', '其', '各', '该', '这', '那',
    }
    filtered = [token for token in tokens if token not in stopwords]
    if not filtered:
        return []

    counts = Counter(filtered)
    ranked = sorted(counts.items(), key=lambda item: (-item[1], filtered.index(item[0]), item[0]))
    return [token for token, _ in ranked[:limit]]


def _build_source_text_fallback(source_file: File, reason: str) -> str:
    return ' '.join(
        [
            source_file.filename,
            f'role={source_file.file_role}',
            f'ext={source_file.ext}',
            f'size={source_file.file_size}',
            reason,
        ]
    )


def _looks_binary_like(sample: bytes) -> bool:
    if not sample:
        return True

    printable = 0
    for byte in sample:
        if byte in (9, 10, 13) or 32 <= byte <= 126:
            printable += 1

    printable_ratio = printable / len(sample)
    if sample.count(b'\x00') > 0:
        return True
    return printable_ratio < _RAG_BINARY_THRESHOLD


def _extract_pdf_text(file_path: Path, max_pages: int = 80) -> str:
    try:
        from pypdf import PdfReader
    except Exception:
        return ''

    try:
        reader = PdfReader(str(file_path))
    except Exception:
        return ''

    texts: list[str] = []
    char_budget = _RAG_SOURCE_CHAR_LIMIT * 2
    total_chars = 0
    for idx, page in enumerate(reader.pages):
        if idx >= max_pages:
            break
        try:
            extracted = (page.extract_text() or '').strip()
        except Exception:
            extracted = ''
        if extracted:
            texts.append(extracted)
            total_chars += len(extracted)
        if total_chars >= char_budget:
            break

    return '\n'.join(texts)


def _read_source_text(source_file: File) -> tuple[str, bool, dict[str, Any]]:
    settings = get_settings()
    file_path = settings.storage_root_path / source_file.storage_path
    if not file_path.exists():
        fallback = _build_source_text_fallback(source_file, 'missing source text')
        return fallback, True, {'source_text_chars': len(fallback), 'truncated': False, 'binary_like': False}

    ext = (source_file.ext or '').strip('.').lower()
    if ext == 'pdf':
        pdf_text = _extract_pdf_text(file_path)
        normalized_pdf = re.sub(r'\s+', ' ', (pdf_text or '')).strip()
        if len(normalized_pdf) >= _RAG_MIN_TEXT_CHARS:
            truncated = False
            if len(normalized_pdf) > _RAG_SOURCE_CHAR_LIMIT:
                normalized_pdf = normalized_pdf[:_RAG_SOURCE_CHAR_LIMIT]
                truncated = True
            return normalized_pdf, False, {
                'source_text_chars': len(normalized_pdf),
                'truncated': truncated,
                'binary_like': False,
                'source_parser': 'pypdf',
            }

    try:
        with file_path.open('rb') as fh:
            raw_bytes = fh.read(_RAG_SOURCE_BYTE_LIMIT + 1)
    except Exception:
        fallback = _build_source_text_fallback(source_file, 'source text unavailable')
        return fallback, True, {'source_text_chars': len(fallback), 'truncated': False, 'binary_like': False}

    truncated = file_path.stat().st_size > len(raw_bytes) or len(raw_bytes) > _RAG_SOURCE_BYTE_LIMIT
    sample = raw_bytes[: min(len(raw_bytes), 64_000)]
    binary_like = _looks_binary_like(sample)

    if binary_like:
        fallback = _build_source_text_fallback(source_file, 'source text unavailable, use metadata fallback')
        return fallback, True, {'source_text_chars': len(fallback), 'truncated': True, 'binary_like': True}

    try:
        raw_text = raw_bytes.decode('utf-8', errors='ignore')
    except Exception:
        raw_text = raw_bytes.decode('latin-1', errors='ignore')

    normalized = re.sub(r'\s+', ' ', raw_text).strip()
    if len(normalized) > _RAG_SOURCE_CHAR_LIMIT:
        normalized = normalized[:_RAG_SOURCE_CHAR_LIMIT]
        truncated = True

    if len(normalized) < _RAG_MIN_TEXT_CHARS:
        fallback = _build_source_text_fallback(source_file, 'source text unavailable, use metadata fallback')
        return fallback, True, {'source_text_chars': len(fallback), 'truncated': True, 'binary_like': binary_like}

    return normalized, False, {
        'source_text_chars': len(normalized),
        'truncated': truncated,
        'binary_like': binary_like,
        'source_parser': 'raw-bytes',
    }


def _load_existing_step_output(
    db: Session,
    task_id: int,
    step_code: str,
    *,
    attempt_no: int = 1,
) -> dict[str, Any] | None:
    step = db.scalar(
        select(TaskStep).where(
            TaskStep.task_id == task_id,
            TaskStep.step_code == step_code,
            TaskStep.attempt_no == attempt_no,
            TaskStep.step_status == TaskStepStatus.SUCCEEDED,
        )
    )
    if not step or not isinstance(step.output_json, dict):
        return None
    return step.output_json


def _build_parse_pdf_step_output(task: Task, source_file: File) -> tuple[dict[str, Any], bool]:
    settings = get_settings()
    source_path = settings.storage_root_path / source_file.storage_path
    if not source_path.exists():
        fallback = _mock_step_output(task, TaskStepCode.PARSE_PDF)
        fallback['analysis_source'] = 'parse_pdf_fallback'
        fallback['fallback_reason'] = 'source file missing'
        fallback['llm_used'] = False
        fallback['fallback_used'] = True
        return fallback, True

    parse_assets_dir = settings.storage_root_path / settings.result_subdir / str(task.user_id) / task.task_no / 'parsed_assets'
    try:
        parsed = parse_pdf_document(
            source_path,
            image_output_dir=parse_assets_dir,
            max_pages=80,
            max_images=48,
            max_tables=48,
        )
    except Exception as exc:
        logger.warning('parse pdf failed, fallback mock used: %s', exc)
        fallback = _mock_step_output(task, TaskStepCode.PARSE_PDF)
        fallback['analysis_source'] = 'parse_pdf_fallback'
        fallback['fallback_reason'] = str(exc)
        fallback['llm_used'] = False
        fallback['fallback_used'] = True
        return fallback, True

    llm_usage: dict[str, Any] | None = None
    llm_error = None
    try:
        llm_refine_output, llm_meta = _call_document_parse_llm(
            task=task,
            source_file=source_file,
            parsed=parsed,
        )
        llm_sections = llm_refine_output.get('sections')
        if isinstance(llm_sections, list):
            normalized_sections: list[dict[str, Any]] = []
            for idx, section in enumerate(llm_sections, start=1):
                if not isinstance(section, dict):
                    continue
                title = _coerce_text(
                    section.get('title') or section.get('name') or section.get('heading'),
                    default=f'Section {idx}',
                )
                try:
                    page = int(section.get('page') or section.get('page_no') or section.get('source_page') or idx)
                except Exception:
                    page = idx
                normalized_sections.append({'title': title, 'page': max(1, page)})
            if normalized_sections:
                parsed['sections'] = normalized_sections

        llm_key_facts = llm_refine_output.get('key_facts') or llm_refine_output.get('facts')
        if isinstance(llm_key_facts, list):
            normalized_facts = [_coerce_text(item) for item in llm_key_facts if _coerce_text(item)]
            if normalized_facts:
                parsed['key_facts'] = normalized_facts[:20]

        llm_evidence = llm_refine_output.get('evidence_spans') or llm_refine_output.get('evidence')
        if isinstance(llm_evidence, list):
            normalized_evidence: list[dict[str, Any]] = []
            for idx, span in enumerate(llm_evidence, start=1):
                if not isinstance(span, dict):
                    continue
                text = _coerce_text(span.get('text') or span.get('excerpt') or span.get('quote'))
                if not text:
                    continue
                try:
                    page = int(span.get('page') or span.get('source_page') or 0)
                except Exception:
                    page = 0
                normalized_evidence.append({'page': max(1, page or idx), 'text': text[:280]})
            if normalized_evidence:
                parsed['evidence_spans'] = normalized_evidence[:24]

        doc_summary = _coerce_text(llm_refine_output.get('doc_summary') or llm_refine_output.get('summary'))
        if doc_summary:
            parsed['doc_summary'] = doc_summary

        llm_usage = llm_meta.get('usage') if isinstance(llm_meta, dict) else None
        parsed['analysis_source'] = 'parse_pdf_llm'
    except Exception as exc:
        llm_error = str(exc)
        logger.warning('parse pdf llm refine failed, keep parser output: %s', exc)
        parsed['analysis_source'] = 'pdf_parse_service'

    parsed['source_file_id'] = source_file.id
    parsed['source_filename'] = source_file.filename
    parsed['source_path'] = str(source_path)
    parsed['image_count'] = len(parsed.get('images') or [])
    parsed['table_count'] = len(parsed.get('tables') or [])
    parsed['analysis_source'] = parsed.get('analysis_source') or 'pdf_parse_service'
    parsed['llm_usage'] = llm_usage
    parsed['llm_error'] = llm_error
    parsed['llm_used'] = bool(llm_usage)
    parsed['fallback_used'] = False
    parsed['fallback_reason'] = None
    return parsed, False


def _build_rag_step_output(task: Task, source_file: File) -> tuple[dict[str, Any], bool]:
    source_text, fallback_used, source_debug = _read_source_text(source_file)
    fallback_keywords = _extract_keywords_from_text(source_text)
    rule_query = build_query(task.user_prompt if task.rag_enabled else '', fallback_keywords)
    query = rule_query
    query_source = 'rule'
    topic_weights: dict[str, float] = {}
    llm_usage: dict[str, Any] | None = None
    llm_error = None
    source_excerpt = source_text[:_LLM_SOURCE_CHAR_LIMIT]
    try:
        llm_rag_output, llm_meta = _call_rag_retrieve_llm(
            task=task,
            source_excerpt=source_excerpt,
            fallback_keywords=fallback_keywords,
            rule_query=rule_query,
        )
        llm_query = _coerce_text(llm_rag_output.get('query') or llm_rag_output.get('retrieval_query'))
        if llm_query:
            query = llm_query
            query_source = 'llm'

        raw_weights = llm_rag_output.get('topic_weights')
        if isinstance(raw_weights, dict):
            normalized_weights: dict[str, float] = {}
            for key, value in raw_weights.items():
                topic = _coerce_text(key)
                if not topic:
                    continue
                try:
                    score = float(value)
                except Exception:
                    continue
                normalized_weights[topic] = round(max(0.0, min(score, 1.0)), 4)
            topic_weights = normalized_weights

        llm_usage = llm_meta.get('usage') if isinstance(llm_meta, dict) else None
    except Exception as exc:
        llm_error = str(exc)
        logger.warning('rag retrieve llm failed, fallback rule query used: %s', exc)

    chunks = chunk_document_text(source_text, chunk_size=400, overlap=60)
    rag_output = retrieve_chunks(chunks, query, top_k=5)
    rag_output.update(
        {
            'source_file_id': source_file.id,
            'source_filename': source_file.filename,
            'source_text_length': len(source_text),
            'source_text_chars': source_debug['source_text_chars'],
            'truncated': source_debug['truncated'],
            'binary_like': source_debug['binary_like'],
            'chunk_count': len(chunks),
            'fallback_text_used': fallback_used,
            'analysis_source': 'rag_llm' if query_source == 'llm' else 'rag_service',
            'query_source': query_source,
            'query': query,
            'rule_query': rule_query,
            'topic_weights': topic_weights or rag_output.get('topic_weights') or {},
            'llm_usage': llm_usage,
            'llm_error': llm_error,
            'llm_used': bool(llm_usage),
            'fallback_used': bool(llm_error),
            'fallback_reason': llm_error,
        }
    )
    return rag_output, fallback_used


def _build_self_correct_step_output(task: Task, step_outputs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    plan_output = step_outputs.get(TaskStepCode.PLAN_SLIDES) or {}
    map_output = step_outputs.get(TaskStepCode.MAP_SLOTS) or {}
    generate_output = step_outputs.get(TaskStepCode.GENERATE_SLIDES) or {}
    slide_plan = plan_output.get('slide_plan') or []
    edit_ops = generate_output.get('edit_ops') or []
    base_mapped_slide_plan = (
        map_output.get('mapped_slide_plan')
        or generate_output.get('mapped_slide_plan')
        or slide_plan
        or []
    )
    result = run_self_correct(slide_plan=slide_plan, edit_ops=edit_ops, detail_level=task.detail_level)
    llm_usage: dict[str, Any] | None = None
    try:
        raw_llm_output, llm_meta = _call_self_correct_llm(
            task=task,
            slide_plan=base_mapped_slide_plan,
            slot_fill_plan=map_output.get('slot_fill_plan') or generate_output.get('slot_fill_plan') or [],
            edit_ops=edit_ops,
        )
        llm_fix_ops = _normalize_llm_fix_ops(raw_llm_output.get('fix_ops') or raw_llm_output.get('applied_fix_ops'))
        if llm_fix_ops:
            result['fix_ops'] = llm_fix_ops

        llm_quality_report = raw_llm_output.get('quality_report')
        if isinstance(llm_quality_report, dict):
            merged_quality_report = dict(result.get('quality_report') or {})
            merged_quality_report.update(llm_quality_report)
            result['quality_report'] = merged_quality_report

        retry_recommended = raw_llm_output.get('retry_recommended')
        if isinstance(retry_recommended, bool):
            result['retry_recommended'] = retry_recommended

        reason_code = _coerce_text(raw_llm_output.get('reason_code') or raw_llm_output.get('fallback_reason'))
        if reason_code:
            result['reason_code'] = reason_code

        llm_usage = llm_meta.get('usage') if isinstance(llm_meta, dict) else None
        result['analysis_source'] = 'self_correct_llm'
        result['llm_usage'] = llm_usage
        result['llm_used'] = True
        result['fallback_used'] = False
        result['fallback_reason'] = None
    except Exception as exc:
        logger.warning('self correct llm failed, fallback rule-based output used: %s', exc)
        result['analysis_source'] = 'self_correct_service'
        result['llm_used'] = False
        result['fallback_used'] = True
        result['fallback_reason'] = str(exc)

    corrected_slide_plan = _apply_fix_ops_to_mapped_slide_plan(
        base_mapped_slide_plan,
        result.get('fix_ops') or [],
    )
    result['mapped_slide_plan'] = corrected_slide_plan or base_mapped_slide_plan
    result['slot_fill_plan'] = map_output.get('slot_fill_plan') or generate_output.get('slot_fill_plan') or []
    result['applied_fix_ops'] = result.get('fix_ops') or []
    return result


def _normalize_fix_ops(fix_ops: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for index, fix_op in enumerate(fix_ops or []):
        if not isinstance(fix_op, dict):
            continue
        op = dict(fix_op)
        op['_order'] = index
        normalized.append(op)

    return sorted(
        normalized,
        key=lambda item: (
            int(item.get('priority') or 999),
            str(item.get('op') or ''),
            str(item.get('target') or ''),
            int(item.get('_order') or 0),
        ),
    )


def _ensure_layout_rules(slide: dict[str, Any]) -> dict[str, Any]:
    layout_schema_json = slide.get('layout_schema_json')
    if not isinstance(layout_schema_json, dict):
        layout_schema_json = {}
        slide['layout_schema_json'] = layout_schema_json

    layout_rules = layout_schema_json.get('layout_rules')
    if not isinstance(layout_rules, dict):
        layout_rules = {}
        layout_schema_json['layout_rules'] = layout_rules

    return layout_rules


def _ensure_style_tokens(slide: dict[str, Any]) -> dict[str, Any]:
    style_tokens_json = slide.get('style_tokens_json')
    if not isinstance(style_tokens_json, dict):
        style_tokens_json = {}
        slide['style_tokens_json'] = style_tokens_json
    return style_tokens_json


def _apply_fix_ops_to_mapped_slide_plan(
    mapped_slide_plan: list[dict[str, Any]],
    fix_ops: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    if not mapped_slide_plan:
        return []

    corrected = deepcopy([slide for slide in mapped_slide_plan if isinstance(slide, dict)])
    if not corrected:
        return []

    for fix_op in _normalize_fix_ops(fix_ops):
        op = str(fix_op.get('op') or '').strip().lower()
        target = str(fix_op.get('target') or '').strip().lower()

        if op in {'noop', 'keep_current_layout'}:
            continue

        for slide in corrected:
            layout_rules = _ensure_layout_rules(slide)
            style_tokens = _ensure_style_tokens(slide)

            bullets = _to_bullet_list(slide.get('bullets'))
            title = _coerce_text(slide.get('title') or slide.get('heading') or slide.get('page_title'))

            if op in {'reduce_text_density', 'shrink_typography', 'font_reduce'}:
                if len(bullets) > 3:
                    slide['bullets'] = bullets[:3]
                elif len(bullets) == 0 and title:
                    slide['bullets'] = [f'Key idea: {title}']
                layout_rules['density_hint'] = 'compact'
                layout_rules['body_text_hint'] = 'compressed'
                style_tokens['font_size_scale'] = min(float(style_tokens.get('font_size_scale') or 1.0), 0.92)
            elif op == 'reflow_layout':
                layout_rules['spacing_hint'] = 'expanded'
                layout_rules['layout_strategy'] = 'reflow'
            elif op == 'separate_visuals_and_text':
                layout_rules['column_strategy'] = 'separated'
                layout_rules['layout_strategy'] = 'visual_text_split'
            elif op == 'fill_empty_space':
                if len(bullets) < 2:
                    filler = title or _coerce_text(slide.get('summary') or slide.get('description') or slide.get('content'))
                    slide['bullets'] = bullets + [filler or 'Supporting detail']
                layout_rules['density_hint'] = 'expanded'
            elif op == 'normalize_alignment':
                layout_rules['alignment_hint'] = 'grid'
                layout_rules['alignment_strategy'] = 'snap_to_grid'
            elif op == 'rebalance_content_density':
                if len(bullets) > 4:
                    slide['bullets'] = bullets[:4]
                layout_rules['density_hint'] = 'balanced'
                layout_rules['density_strategy'] = 'redistribute'
            elif op == 'standardize_titles':
                if title:
                    normalized_title = re.sub(r'\s+', ' ', title).strip()
                    if len(normalized_title) > 72:
                        normalized_title = normalized_title[:69].rstrip() + '...'
                    slide['title'] = normalized_title
                layout_rules['title_style'] = 'standardized'
            elif op == 'global_rebalance':
                layout_rules['rebalance_scope'] = 'global'
                if target in {'deck', 'content'}:
                    layout_rules['density_hint'] = 'balanced'
                    layout_rules['layout_strategy'] = 'global_rebalance'
            elif op == 'selective_rebalance':
                layout_rules['rebalance_scope'] = 'selective'
                if target in {'deck', 'content'}:
                    layout_rules['density_hint'] = 'balanced'
                    layout_rules['layout_strategy'] = 'selective_rebalance'
            else:
                layout_rules.setdefault('self_correct_ops', [])
                if op and op not in layout_rules['self_correct_ops']:
                    layout_rules['self_correct_ops'].append(op)

    return corrected


def _build_slot_type_counts(slot_fill_plan: list[dict[str, Any]]) -> dict[int, dict[str, int]]:
    counts: dict[int, dict[str, int]] = {}
    for item in slot_fill_plan:
        if not isinstance(item, dict):
            continue
        slide_no = int(item.get('slide_no') or 0)
        if slide_no <= 0:
            continue
        slot_type = str(item.get('slot_type') or 'text').lower()
        bucket = counts.setdefault(slide_no, {'text': 0, 'image': 0, 'table': 0})
        if slot_type in bucket:
            bucket[slot_type] += 1
    return counts


def _collect_quality_page_metrics(
    mapped_slide_plan: list[dict[str, Any]],
    slot_fill_plan: list[dict[str, Any]],
) -> dict[str, Any]:
    slot_counts = _build_slot_type_counts(slot_fill_plan)
    excluded_page_types = {'cover', 'toc'}

    evaluated_pages = 0
    editable_pages = 0
    locked_pages = 0
    page_diagnostics: list[dict[str, Any]] = []

    for idx, slide in enumerate(mapped_slide_plan, start=1):
        if not isinstance(slide, dict):
            continue

        slide_no = int(slide.get('page_no') or slide.get('slide_no') or idx)
        page_function = str(slide.get('page_function') or 'content').lower()
        if page_function in excluded_page_types:
            continue

        evaluated_pages += 1
        title = _coerce_text(slide.get('title') or slide.get('heading') or slide.get('page_title'))
        bullets = _to_bullet_list(slide.get('bullets'))
        slot_bucket = slot_counts.get(slide_no, {'text': 0, 'image': 0, 'table': 0})

        text_units = (1 if title else 0) + len(bullets) + slot_bucket.get('text', 0)
        visual_units = slot_bucket.get('image', 0) + slot_bucket.get('table', 0)
        editable = text_units > 0
        locked = text_units == 0 and visual_units > 0

        if editable:
            editable_pages += 1
        if locked:
            locked_pages += 1

        page_diagnostics.append(
            {
                'page_no': slide_no,
                'page_function': page_function,
                'text_units': text_units,
                'visual_units': visual_units,
                'editable': editable,
                'locked': locked,
            }
        )

    def _ratio(count: int, total: int) -> float:
        return round(count / total, 4) if total else 0.0

    return {
        'evaluated_pages': evaluated_pages,
        'editable_text_ratio': _ratio(editable_pages, evaluated_pages),
        'locked_page_ratio': _ratio(locked_pages, evaluated_pages),
        'page_diagnostics': page_diagnostics,
        'slot_counts': slot_counts,
        'excluded_page_types': sorted(excluded_page_types),
    }


def _resolve_export_slide_plan(step_outputs: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    self_correct_output = step_outputs.get(TaskStepCode.SELF_CORRECT) or {}
    generate_output = step_outputs.get(TaskStepCode.GENERATE_SLIDES) or {}
    map_output = step_outputs.get(TaskStepCode.MAP_SLOTS) or {}
    plan_output = step_outputs.get(TaskStepCode.PLAN_SLIDES) or {}
    return (
        self_correct_output.get('mapped_slide_plan')
        or generate_output.get('mapped_slide_plan')
        or map_output.get('mapped_slide_plan')
        or plan_output.get('slide_plan')
        or []
    )


def _extract_json_object(content: str) -> dict[str, Any] | None:
    if not content:
        return None

    candidate = content.strip()
    try:
        parsed = json.loads(candidate)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass

    start = candidate.find('{')
    end = candidate.rfind('}')
    if start == -1 or end == -1 or end <= start:
        return None

    try:
        parsed = json.loads(candidate[start : end + 1])
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        return None
    return None


def _to_bullet_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, dict):
        return [str(v).strip() for v in value.values() if str(v).strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _overflow_policy(detail_level: str) -> dict[str, int]:
    level = (detail_level or 'balanced').strip().lower()
    if level == 'concise':
        return {'max_bullets': 4, 'max_chars': 260, 'max_bullet_chars': 88}
    if level == 'detailed':
        return {'max_bullets': 8, 'max_chars': 580, 'max_bullet_chars': 150}
    return {'max_bullets': 6, 'max_chars': 420, 'max_bullet_chars': 120}


def _summarize_bullet_text(text: str, max_chars: int) -> tuple[str, bool]:
    value = _coerce_text(text)
    if len(value) <= max_chars:
        return value, False
    cut = value[: max(0, max_chars - 1)].rstrip()
    if not cut:
        return value[:max_chars], True
    return f'{cut}…', True


def _split_bullets_by_policy(bullets: list[str], policy: dict[str, int]) -> tuple[list[list[str]], int]:
    max_bullets = int(policy.get('max_bullets') or 6)
    max_chars = int(policy.get('max_chars') or 420)
    max_bullet_chars = int(policy.get('max_bullet_chars') or 120)

    normalized: list[str] = []
    summary_count = 0
    for bullet in bullets:
        summarized, changed = _summarize_bullet_text(bullet, max_bullet_chars)
        normalized.append(summarized)
        if changed:
            summary_count += 1

    chunks: list[list[str]] = []
    current: list[str] = []
    current_chars = 0
    for bullet in normalized:
        bullet_len = len(bullet)
        exceeds_count = len(current) >= max_bullets
        exceeds_chars = current_chars + bullet_len > max_chars and bool(current)
        if exceeds_count or exceeds_chars:
            chunks.append(current)
            current = []
            current_chars = 0
        current.append(bullet)
        current_chars += bullet_len
    if current:
        chunks.append(current)
    if not chunks:
        chunks = [normalized[:max_bullets] or ['Key facts']]
    return chunks, summary_count


def _apply_text_overflow_strategy(mapped_slide_plan: list[dict[str, Any]], detail_level: str) -> tuple[list[dict[str, Any]], dict[str, int]]:
    if not mapped_slide_plan:
        return [], {'split_pages': 0, 'summary_applied': 0, 'font_scale_applied': 0}

    policy = _overflow_policy(detail_level)
    result: list[dict[str, Any]] = []
    split_pages = 0
    summary_applied = 0
    font_scale_applied = 0

    for idx, slide in enumerate(mapped_slide_plan, start=1):
        if not isinstance(slide, dict):
            continue
        bullets = _to_bullet_list(slide.get('bullets'))
        title = _coerce_text(slide.get('title') or slide.get('heading') or slide.get('page_title') or f'Page {idx}')
        if not bullets:
            bullets = ['Key facts', 'Insights', 'Recommendations']

        chunks, summarized_count = _split_bullets_by_policy(bullets, policy)
        summary_applied += summarized_count

        for chunk_index, chunk in enumerate(chunks, start=1):
            cloned = deepcopy(slide)
            cloned['bullets'] = chunk
            if chunk_index == 1:
                cloned['title'] = title
            else:
                cloned['title'] = f'{title} (Continued {chunk_index})'
                # Split page priority: continuation pages are text-first, avoid duplicating heavy assets.
                cloned['tables'] = []
                cloned['images'] = []
                split_pages += 1

            total_chars = sum(len(item) for item in chunk)
            if total_chars > int(policy.get('max_chars') or 420):
                style_tokens = cloned.get('style_tokens_json')
                if not isinstance(style_tokens, dict):
                    style_tokens = {}
                    cloned['style_tokens_json'] = style_tokens
                current_scale = float(style_tokens.get('font_size_scale') or 1.0)
                style_tokens['font_size_scale'] = min(current_scale, 0.92)
                font_scale_applied += 1
            result.append(cloned)

    for page_no, slide in enumerate(result, start=1):
        slide['page_no'] = page_no

    return result, {
        'split_pages': split_pages,
        'summary_applied': summary_applied,
        'font_scale_applied': font_scale_applied,
    }


def _build_default_slide_plan(page_count: int) -> list[dict[str, Any]]:
    slides: list[dict[str, Any]] = []
    for page_no in range(1, page_count + 1):
        if page_no == 1:
            title = 'Cover'
            bullets = ['Project background', 'Presenter', 'Date']
        elif page_no == 2:
            title = 'Agenda'
            bullets = ['Key conclusions', 'Core analysis', 'Action items']
        elif page_no == page_count:
            title = 'Summary and Actions'
            bullets = ['Conclusion recap', 'Priority items', 'Next steps']
        else:
            title = f'Page {page_no} Core Content'
            bullets = ['Key facts', 'Insights', 'Recommendations']
        slides.append({'page_no': page_no, 'title': title, 'bullets': bullets})
    return slides


def _normalize_slide_plan(raw_slides: Any, page_count: int) -> list[dict[str, Any]]:
    items = raw_slides if isinstance(raw_slides, list) else []
    normalized: list[dict[str, Any]] = []

    for idx in range(page_count):
        raw = items[idx] if idx < len(items) and isinstance(items[idx], dict) else {}
        page_no = idx + 1
        title = str(
            raw.get('title')
            or raw.get('heading')
            or raw.get('page_title')
            or raw.get('name')
            or f'Page {page_no}'
        ).strip()
        bullets = _to_bullet_list(raw.get('bullets') or raw.get('points') or raw.get('items'))
        if not bullets:
            bullets = ['Key facts', 'Insights', 'Recommendations']
        normalized.append({'page_no': page_no, 'title': title, 'bullets': bullets[:6]})

    return normalized


def _build_plan_slides_step_output(
    task: Task,
    source_file: File,
    step_outputs: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], bool]:
    low, high = DETAIL_LEVEL_PAGE_RANGE.get(task.detail_level, (13, 20))
    target_page_count = task.page_count_estimated or ((low + high) // 2)
    target_page_count = max(low, min(high, int(target_page_count)))

    source_text, fallback_used, source_debug = _read_source_text(source_file)
    source_excerpt = source_text[:_LLM_SOURCE_CHAR_LIMIT]

    rag_output = step_outputs.get(TaskStepCode.RAG_RETRIEVE) or {}
    rag_chunks = rag_output.get('retrieved_chunks') or []
    rag_context: list[str] = []
    for item in rag_chunks[:5]:
        if not isinstance(item, dict):
            continue
        excerpt = str(item.get('excerpt') or item.get('text') or '').strip()
        if excerpt:
            rag_context.append(excerpt[:220])

    system_prompt = (
        'You are a presentation planning assistant. '
        'Return JSON only. '
        'Schema: {"page_count_estimated": int, "slides": [{"title": str, "bullets": [str]}]}. '
        'Keep bullets concise and business-oriented.'
    )

    user_prompt = (
        f'detail_level={task.detail_level}\n'
        f'target_page_count={target_page_count}\n'
        f'user_prompt={(task.user_prompt or "").strip()}\n'
        f'context_text={source_excerpt}\n'
        f'rag_context={rag_context}'
    )

    try:
        result = call_chat_completions(
            messages=[
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_prompt},
            ],
            temperature=0.2,
            max_tokens=2200,
        )
        parsed = _extract_json_object(result.content)
        if not parsed:
            raise ValueError('llm returned non-json content')

        requested_page_count = int(parsed.get('page_count_estimated') or target_page_count)
        requested_page_count = max(low, min(high, requested_page_count))
        slide_plan = _normalize_slide_plan(parsed.get('slides') or parsed.get('slide_plan'), requested_page_count)

        return {
            'slide_plan': slide_plan,
            'page_count_estimated': len(slide_plan),
            'plan_source': 'llm',
            'llm_usage': result.usage,
            'llm_used': True,
            'fallback_used': False,
            'fallback_reason': None,
            'source_text_chars': source_debug.get('source_text_chars'),
            'fallback_text_used': fallback_used,
            'rag_context_count': len(rag_context),
        }, False
    except Exception as exc:
        logger.warning('plan slides llm failed, fallback mock used: %s', exc)
        fallback = _mock_step_output(task, TaskStepCode.PLAN_SLIDES)
        fallback['plan_source'] = 'mock_fallback'
        fallback['fallback_reason'] = str(exc)
        fallback['source_text_chars'] = source_debug.get('source_text_chars')
        fallback['fallback_text_used'] = fallback_used
        fallback['llm_used'] = False
        fallback['fallback_used'] = True
        return fallback, True


def _build_generate_slides_step_output(
    db: Session,
    task: Task,
    step_outputs: dict[str, dict[str, Any]],
    template_profile_id: int | None,
) -> tuple[dict[str, Any], bool]:
    map_output = step_outputs.get(TaskStepCode.MAP_SLOTS) or {}
    mapped_slide_plan = map_output.get('mapped_slide_plan') or []
    plan_output = step_outputs.get(TaskStepCode.PLAN_SLIDES) or {}
    slide_plan = plan_output.get('slide_plan') or []

    if not slide_plan:
        fallback_plan = _mock_step_output(task, TaskStepCode.PLAN_SLIDES)
        slide_plan = fallback_plan.get('slide_plan') or []

    if not slide_plan and not mapped_slide_plan:
        fallback = _mock_step_output(task, TaskStepCode.GENERATE_SLIDES)
        fallback['generation_source'] = 'mock_fallback'
        return fallback, True

    mapping = {
        'mapping_mode': map_output.get('mapping_mode'),
        'template_page_count': map_output.get('template_page_count'),
    }
    if not mapped_slide_plan:
        template_pages: list[dict[str, Any]] = []
        if template_profile_id:
            rows = list(
                db.scalars(
                    select(TemplatePageSchema)
                    .where(TemplatePageSchema.template_profile_id == template_profile_id)
                    .order_by(TemplatePageSchema.page_no.asc())
                ).all()
            )
            template_pages = [
                {
                    'page_no': row.page_no,
                    'cluster_label': row.cluster_label,
                    'page_function': row.page_function,
                    'layout_schema_json': row.layout_schema_json,
                    'style_tokens_json': row.style_tokens_json,
                }
                for row in rows
            ]

        mapping = map_slide_plan_to_template(slide_plan=slide_plan, template_pages=template_pages)
        mapped_slide_plan = mapping.get('mapped_slide_plan') or []

    rule_based_edit_ops: list[dict[str, Any]] = []
    for idx, slide in enumerate(mapped_slide_plan, start=1):
        page_no = int(slide.get('page_no') or idx)
        title = str(slide.get('title') or f'Page {page_no}').strip()
        bullets = _to_bullet_list(slide.get('bullets'))
        if not bullets:
            bullets = ['Key facts', 'Insights', 'Recommendations']

        rule_based_edit_ops.append(
            {
                'op': 'replace_title',
                'page_no': page_no,
                'value': title,
                'page_function': slide.get('page_function'),
                'cluster_label': slide.get('cluster_label'),
            }
        )
        rule_based_edit_ops.append(
            {
                'op': 'replace_bullets',
                'page_no': page_no,
                'value': bullets[:6],
                'layout_schema_json': slide.get('layout_schema_json'),
                'style_tokens_json': slide.get('style_tokens_json'),
            }
        )
        tables = slide.get('tables') or []
        images = slide.get('images') or []
        if tables:
            rule_based_edit_ops.append(
                {
                    'op': 'render_tables',
                    'page_no': page_no,
                    'count': len(tables),
                    'slot_keys': [str(item.get('slot_key') or '') for item in tables],
                }
            )
        if images:
            rule_based_edit_ops.append(
                {
                    'op': 'render_images',
                    'page_no': page_no,
                    'count': len(images),
                    'slot_keys': [str(item.get('slot_key') or '') for item in images],
                }
            )

    generation_source = 'rule_based_from_plan'
    llm_usage: dict[str, Any] | None = None
    llm_error: str | None = None
    analyze_output = step_outputs.get(TaskStepCode.ANALYZE_TEMPLATE) or {}
    raw_llm_suggestions = []
    if isinstance(analyze_output, dict):
        raw_llm_suggestions = analyze_output.get('llm_page_suggestions') or []
        if not isinstance(raw_llm_suggestions, list):
            raw_llm_suggestions = []
        if not raw_llm_suggestions:
            summary_json = analyze_output.get('summary_json')
            if isinstance(summary_json, dict):
                fallback_suggestions = summary_json.get('llm_page_suggestions')
                if isinstance(fallback_suggestions, list):
                    raw_llm_suggestions = fallback_suggestions
    llm_overlay = apply_template_llm_suggestions(
        mapped_slide_plan=mapped_slide_plan,
        llm_page_suggestions=raw_llm_suggestions,
    )
    mapped_slide_plan = llm_overlay.get('mapped_slide_plan') or mapped_slide_plan
    mapped_slide_plan = _inject_slide_assets(
        mapped_slide_plan=mapped_slide_plan,
        slot_fill_plan=map_output.get('slot_fill_plan') or [],
    )

    try:
        raw_llm_output, llm_meta = _call_generate_slides_llm(
            task=task,
            mapped_slide_plan=mapped_slide_plan,
            slot_fill_plan=map_output.get('slot_fill_plan') or [],
        )
        llm_edit_ops = raw_llm_output.get('edit_ops') or raw_llm_output.get('ops')
        if isinstance(llm_edit_ops, list):
            edit_ops = [item for item in llm_edit_ops if isinstance(item, dict)]
        else:
            edit_ops = []

        llm_mapped_slide_plan = raw_llm_output.get('mapped_slide_plan') or raw_llm_output.get('slide_plan')
        if isinstance(llm_mapped_slide_plan, list):
            llm_by_page: dict[int, dict[str, Any]] = {}
            for item in llm_mapped_slide_plan:
                if not isinstance(item, dict):
                    continue
                try:
                    page_no = int(item.get('page_no') or item.get('slide_no') or 0)
                except Exception:
                    page_no = 0
                if page_no > 0:
                    llm_by_page[page_no] = item

            merged_plan: list[dict[str, Any]] = []
            for idx, slide in enumerate(mapped_slide_plan, start=1):
                page_no = int(slide.get('page_no') or idx)
                merged = dict(slide)
                override = llm_by_page.get(page_no)
                if override:
                    for key in (
                        'title',
                        'bullets',
                        'page_function',
                        'cluster_label',
                        'template_page_no',
                        'layout_schema_json',
                        'style_tokens_json',
                        'tables',
                        'images',
                    ):
                        value = override.get(key)
                        if value not in (None, '', []):
                            merged[key] = value
                merged_plan.append(merged)
            mapped_slide_plan = merged_plan

        if not edit_ops:
            edit_ops = rule_based_edit_ops
        generation_source = 'llm'
        llm_usage = llm_meta.get('usage') if isinstance(llm_meta, dict) else None
    except Exception as exc:
        llm_error = str(exc)
        logger.warning('generate slides llm failed, fallback rule-based output used: %s', exc)
        edit_ops = rule_based_edit_ops
        generation_source = 'rule_based_from_plan'

    mapped_slide_plan, text_overflow_stats = _apply_text_overflow_strategy(mapped_slide_plan, task.detail_level)
    if text_overflow_stats.get('split_pages'):
        # Rebuild rule-based ops on split pages to keep export edits aligned with final page structure.
        rebuilt_ops: list[dict[str, Any]] = []
        for idx, slide in enumerate(mapped_slide_plan, start=1):
            page_no = int(slide.get('page_no') or idx)
            title = str(slide.get('title') or f'Page {page_no}').strip()
            bullets = _to_bullet_list(slide.get('bullets')) or ['Key facts', 'Insights', 'Recommendations']
            rebuilt_ops.append(
                {
                    'op': 'replace_title',
                    'page_no': page_no,
                    'value': title,
                    'page_function': slide.get('page_function'),
                    'cluster_label': slide.get('cluster_label'),
                }
            )
            rebuilt_ops.append(
                {
                    'op': 'replace_bullets',
                    'page_no': page_no,
                    'value': bullets[:8],
                    'layout_schema_json': slide.get('layout_schema_json'),
                    'style_tokens_json': slide.get('style_tokens_json'),
                }
            )
        edit_ops = rebuilt_ops

    return {
        'edit_ops': edit_ops,
        'page_count': len(mapped_slide_plan),
        'generation_source': generation_source,
        'mapping_mode': mapping.get('mapping_mode'),
        'template_page_count': mapping.get('template_page_count'),
        'mapped_slide_plan': mapped_slide_plan,
        'llm_suggestions_total': llm_overlay.get('suggestions_total', 0),
        'llm_suggestions_applied': llm_overlay.get('suggestions_applied', 0),
        'llm_usage': llm_usage,
        'llm_error': llm_error,
        'text_overflow_strategy': text_overflow_stats,
        'llm_used': generation_source == 'llm',
        'fallback_used': generation_source != 'llm',
        'fallback_reason': llm_error,
    }, False


def _mock_step_output(task: Task, step_code: str) -> dict:
    if step_code == TaskStepCode.PARSE_PDF:
        return {
            'sections': [
                {'title': 'Background', 'page': 1},
                {'title': 'Analysis', 'page': 2},
                {'title': 'Conclusion', 'page': 3},
            ],
            'key_facts': ['fact_a', 'fact_b'],
            'evidence_spans': [{'page': 2, 'text': 'sample evidence'}],
            'images': [],
            'tables': [],
            'analysis_source': 'mock_default',
        }
    if step_code == TaskStepCode.RAG_RETRIEVE:
        return {
            'retrieved_chunks': [{'chunk_id': 'c1', 'score': 0.86}],
            'citations': [{'source_page': 5}],
            'topic_weights': {'market': 0.8},
        }
    if step_code == TaskStepCode.PLAN_SLIDES:
        base_count = {'concise': 10, 'balanced': 16, 'detailed': 24}.get(task.detail_level, 16)
        return {
            'slide_plan': _build_default_slide_plan(base_count),
            'page_count_estimated': base_count,
            'plan_source': 'mock_default',
        }
    if step_code == TaskStepCode.ASSETIZE_TEMPLATE:
        return {
            'asset_pages': [],
            'slots': [],
            'asset_pages_count': 0,
            'slots_count': 0,
            'analysis_source': 'mock_default',
        }
    if step_code == TaskStepCode.MAP_SLOTS:
        return {
            'page_mappings': [],
            'slot_fill_plan': [],
            'mapped_slide_plan': [],
            'analysis_source': 'mock_default',
        }
    if step_code == TaskStepCode.GENERATE_SLIDES:
        return {
            'edit_ops': [{'op': 'replace_text', 'target': 'title', 'value': 'Generated by BetterPPT'}],
            'generation_source': 'mock_default',
        }
    if step_code == TaskStepCode.SELF_CORRECT:
        return {'fix_ops': [{'op': 'font_reduce'}], 'quality_report': {'overflow': False, 'collision': False}}
    if step_code == TaskStepCode.EXPORT_PPT:
        return {'filename': f'result_{task.task_no}.pptx'}
    return {'ok': True}


def _call_template_service(db: Session, task: Task, reference_file: File) -> dict[str, Any] | None:
    try:
        module = importlib.import_module('app.services.template_service')
    except Exception as exc:  # pragma: no cover - optional dependency
        logger.warning('template service unavailable: %s', exc)
        return None

    func = getattr(module, 'analyze_and_persist_template', None)
    if callable(func):
        call_attempts = (
            lambda: func(db=db, reference_file=reference_file, detail_level=task.detail_level, task_no=task.task_no),
            lambda: func(db, reference_file, task.detail_level, task.task_no),
            lambda: func(reference_file=reference_file, detail_level=task.detail_level, task_no=task.task_no, db=db),
        )
        for attempt in call_attempts:
            try:
                result = attempt()
                if isinstance(result, dict):
                    return result
                if hasattr(result, 'id'):
                    return {
                        'profile_id': getattr(result, 'id', None),
                        'profile_version': getattr(result, 'profile_version', 'v1'),
                        'total_pages': getattr(result, 'total_pages', 0),
                        'cluster_count': getattr(result, 'cluster_count', 0),
                        'embedding_model': getattr(result, 'embedding_model', 'vit-base'),
                        'llm_model': getattr(result, 'llm_model', get_settings().llm_model),
                        'pages': [],
                        'summary_json': getattr(result, 'summary_json', None),
                    }
            except TypeError:
                continue
            except Exception as exc:  # pragma: no cover - optional service fallback
                logger.warning('template service call failed: %s', exc)
                break

    candidate_names = (
        'analyze_template',
        'analyze_template_profile',
        'analyze_reference_ppt',
        'build_template_profile',
    )
    for name in candidate_names:
        func = getattr(module, name, None)
        if not callable(func):
            continue
        call_attempts = (
            lambda: func(task=task, reference_file=reference_file),
            lambda: func(reference_file=reference_file, task=task),
            lambda: func(reference_file=reference_file, task_context=task),
            lambda: func(task, reference_file),
            lambda: func(reference_file),
        )
        for attempt in call_attempts:
            try:
                result = attempt()
                if isinstance(result, dict):
                    return result
                if hasattr(result, 'id'):
                    return {
                        'profile_id': getattr(result, 'id', None),
                        'profile_version': getattr(result, 'profile_version', 'v1'),
                        'total_pages': getattr(result, 'total_pages', 0),
                        'cluster_count': getattr(result, 'cluster_count', 0),
                        'embedding_model': getattr(result, 'embedding_model', 'vit-base'),
                        'llm_model': getattr(result, 'llm_model', get_settings().llm_model),
                        'pages': [],
                        'summary_json': getattr(result, 'summary_json', None),
                    }
            except TypeError:
                continue
            except Exception as exc:  # pragma: no cover - optional service fallback
                logger.warning('template service call failed: %s', exc)
                break
    return None


def _build_mock_template_analysis(task: Task, reference_file: File) -> dict[str, Any]:
    settings = get_settings()
    seed_basis = f'{reference_file.filename}:{reference_file.file_size}:{task.detail_level}'
    total_pages = 4 + (sum(ord(ch) for ch in seed_basis) % 5)
    total_pages = max(4, min(12, total_pages))
    cluster_count = max(2, min(4, 2 + (total_pages // 4)))

    pages: list[dict[str, Any]] = []
    for page_no in range(1, total_pages + 1):
        if page_no == 1:
            page_function = 'cover'
            layout_schema_json = {'slots': ['title', 'subtitle', 'hero']}
        elif page_no == 2:
            page_function = 'toc'
            layout_schema_json = {'slots': ['toc_title', 'toc_items']}
        elif page_no == total_pages:
            page_function = 'summary'
            layout_schema_json = {'slots': ['summary_title', 'summary_points', 'action_items']}
        elif page_no % 4 == 0:
            page_function = 'comparison'
            layout_schema_json = {'slots': ['left_panel', 'right_panel', 'caption']}
        else:
            page_function = 'content'
            layout_schema_json = {'slots': ['heading', 'bullets', 'supporting_visual']}

        cluster_index = (page_no - 1) % cluster_count + 1
        pages.append(
            {
                'page_no': page_no,
                'cluster_label': f'cluster_{cluster_index}',
                'page_function': page_function,
                'layout_schema_json': layout_schema_json,
                'style_tokens_json': {
                    'theme': reference_file.filename.rsplit('.', 1)[0][:24] or 'template',
                    'font_family': 'Arial',
                    'accent_color': '#1b6ef3',
                    'background_color': '#f7faff',
                    'text_color': '#1e2b39',
                    'detail_level': task.detail_level,
                },
            }
        )

    return {
        'profile_version': 'v1',
        'total_pages': total_pages,
        'cluster_count': cluster_count,
        'embedding_model': 'vit-base',
        'llm_model': settings.llm_model,
        'pages': pages,
        'summary_json': {
            'source_file': reference_file.filename,
            'detail_level': task.detail_level,
            'total_pages': total_pages,
            'cluster_count': cluster_count,
        },
    }


def _analyze_template(db: Session, task: Task, reference_file: File) -> tuple[dict[str, Any], bool]:
    service_output = _call_template_service(db, task, reference_file)
    if service_output:
        return service_output, False
    return _build_mock_template_analysis(task, reference_file), True


def _normalize_template_analysis(task: Task, reference_file: File, raw: dict[str, Any]) -> dict[str, Any]:
    pages = raw.get('pages') or []
    if not pages:
        fallback = _build_mock_template_analysis(task, reference_file)
        raw = {**fallback, **raw}
        pages = raw['pages']

    return {
        'profile_version': raw.get('profile_version') or 'v1',
        'total_pages': int(raw.get('total_pages') or len(pages) or 4),
        'cluster_count': int(raw.get('cluster_count') or max(2, min(4, len(pages) // 2 or 2))),
        'embedding_model': raw.get('embedding_model') or 'vit-base',
        'llm_model': raw.get('llm_model') or get_settings().llm_model,
        'pages': pages,
        'summary_json': raw.get('summary_json') or {
            'source_file': reference_file.filename,
            'detail_level': task.detail_level,
            'total_pages': int(raw.get('total_pages') or len(pages) or 4),
            'cluster_count': int(raw.get('cluster_count') or max(2, min(4, len(pages) // 2 or 2))),
        },
    }


def _upsert_template_profile(db: Session, task: Task, reference_file: File, analysis: dict[str, Any]) -> TemplateProfile:
    profile_version = analysis['profile_version']
    profile = db.scalar(
        select(TemplateProfile).where(
            TemplateProfile.file_id == reference_file.id,
            TemplateProfile.profile_version == profile_version,
        )
    )
    if profile:
        db.execute(delete(TemplatePageSchema).where(TemplatePageSchema.template_profile_id == profile.id))
    else:
        profile = TemplateProfile(
            file_id=reference_file.id,
            profile_version=profile_version,
            total_pages=analysis['total_pages'],
            cluster_count=analysis['cluster_count'],
            embedding_model=analysis['embedding_model'],
            llm_model=analysis['llm_model'],
            summary_json=analysis['summary_json'],
        )
        db.add(profile)
        db.flush()

    profile.total_pages = analysis['total_pages']
    profile.cluster_count = analysis['cluster_count']
    profile.embedding_model = analysis['embedding_model']
    profile.llm_model = analysis['llm_model']
    profile.summary_json = analysis['summary_json']

    for page in analysis['pages']:
        db.add(
            TemplatePageSchema(
                template_profile_id=profile.id,
                page_no=page['page_no'],
                cluster_label=page['cluster_label'],
                page_function=page['page_function'],
                layout_schema_json=page['layout_schema_json'],
                style_tokens_json=page.get('style_tokens_json'),
            )
        )

    db.flush()
    return profile


def _infer_slot_type(slot_key: str) -> str:
    value = str(slot_key or '').lower()
    if any(token in value for token in ('image', 'visual', 'hero', 'figure', 'photo', 'chart')):
        return 'image'
    if any(token in value for token in ('table', 'datatable', 'grid')):
        return 'table'
    return 'text'


def _infer_slot_role(slot_key: str) -> str:
    value = str(slot_key or '').lower()
    if 'title' in value:
        return 'title'
    if 'subtitle' in value:
        return 'subtitle'
    if any(token in value for token in ('bullet', 'body', 'points', 'summary', 'agenda', 'list')):
        return 'bullet'
    if any(token in value for token in ('table', 'datatable', 'grid')):
        return 'datatable'
    if any(token in value for token in ('image', 'visual', 'hero', 'figure', 'photo', 'chart')):
        return 'figure'
    return 'summary'


def _normalize_layout_slots(layout_schema_json: dict[str, Any] | None) -> list[dict[str, Any]]:
    layout_schema_json = layout_schema_json if isinstance(layout_schema_json, dict) else {}
    raw_slots = layout_schema_json.get('slots') or []
    normalized: list[dict[str, Any]] = []
    for index, raw_slot in enumerate(raw_slots, start=1):
        if isinstance(raw_slot, dict):
            slot_key = str(raw_slot.get('slot_key') or raw_slot.get('name') or f'slot_{index}')
            slot_type = str(raw_slot.get('slot_type') or _infer_slot_type(slot_key))
            slot_role = str(raw_slot.get('slot_role') or _infer_slot_role(slot_key))
        else:
            slot_key = str(raw_slot or f'slot_{index}')
            slot_type = _infer_slot_type(slot_key)
            slot_role = _infer_slot_role(slot_key)
        normalized.append(
            {
                'slot_key': slot_key,
                'slot_type': slot_type,
                'slot_role': slot_role,
                'slot_index': index,
            }
        )
    if normalized:
        return normalized
    return [
        {
            'slot_key': 'title',
            'slot_type': 'text',
            'slot_role': 'title',
            'slot_index': 1,
        },
        {
            'slot_key': 'body',
            'slot_type': 'text',
            'slot_role': 'bullet',
            'slot_index': 2,
        },
    ]


def _slot_bbox(slot_index: int, slot_type: str) -> tuple[float, float, float, float]:
    if slot_type == 'image':
        width = 0.36
        height = 0.28
    elif slot_type == 'table':
        width = 0.52
        height = 0.24
    else:
        width = 0.46
        height = 0.16 if slot_index == 1 else 0.22
    column = 0 if slot_index % 2 else 1
    row = max(0, (slot_index - 1) // 2)
    x = 0.08 + column * 0.42
    y = 0.14 + row * 0.20
    return (round(x, 4), round(y, 4), round(width, 4), round(height, 4))


def _upsert_template_slot_definitions(db: Session, profile_id: int, pages: list[dict[str, Any]]) -> int:
    db.execute(delete(TemplateSlotDefinition).where(TemplateSlotDefinition.template_profile_id == profile_id))
    inserted = 0
    for page in pages:
        page_no = int(page.get('page_no') or 0)
        for slot in _normalize_layout_slots(page.get('layout_schema_json')):
            bbox_x, bbox_y, bbox_w, bbox_h = _slot_bbox(int(slot['slot_index']), str(slot['slot_type']))
            db.add(
                TemplateSlotDefinition(
                    template_profile_id=profile_id,
                    page_no=page_no,
                    slot_key=str(slot['slot_key']),
                    slot_type=str(slot['slot_type']),
                    slot_role=str(slot['slot_role']),
                    bbox_x=bbox_x,
                    bbox_y=bbox_y,
                    bbox_w=bbox_w,
                    bbox_h=bbox_h,
                    z_index=int(slot['slot_index']),
                    style_tokens_json=page.get('style_tokens_json') or {},
                    constraints_json={'derived_from': 'layout_schema_json'},
                )
            )
            inserted += 1
    db.flush()
    return inserted


def _build_assetize_step_output(
    db: Session,
    task: Task,
    *,
    template_profile_id: int | None,
) -> dict[str, Any]:
    if not template_profile_id:
        return {
            'asset_pages': [],
            'slots': [],
            'style_tokens': {},
            'layout_semantics': {},
            'asset_pages_count': 0,
            'slots_count': 0,
        }

    rows = list(
        db.scalars(
            select(TemplatePageSchema)
            .where(TemplatePageSchema.template_profile_id == template_profile_id)
            .order_by(TemplatePageSchema.page_no.asc())
        ).all()
    )
    asset_pages: list[dict[str, Any]] = []
    flat_slots: list[dict[str, Any]] = []
    for row in rows:
        slots = _normalize_layout_slots(row.layout_schema_json if isinstance(row.layout_schema_json, dict) else {})
        asset_page = {
            'page_no': row.page_no,
            'cluster_label': row.cluster_label,
            'page_function': row.page_function,
            'layout_schema_json': row.layout_schema_json,
            'style_tokens_json': row.style_tokens_json,
            'slots': slots,
        }
        asset_pages.append(asset_page)
        for slot in slots:
            flat_slots.append(
                {
                    'page_no': row.page_no,
                    'slot_key': slot['slot_key'],
                    'slot_type': slot['slot_type'],
                    'slot_role': slot['slot_role'],
                }
            )

    inserted_slots = _upsert_template_slot_definitions(db, template_profile_id, asset_pages)
    page_functions = sorted({page['page_function'] for page in asset_pages})
    return {
        'profile_id': template_profile_id,
        'asset_pages': asset_pages,
        'slots': flat_slots,
        'style_tokens': asset_pages[0].get('style_tokens_json') if asset_pages else {},
        'layout_semantics': {'page_functions': page_functions},
        'asset_pages_count': len(asset_pages),
        'slots_count': inserted_slots,
        'analysis_source': 'assetized_from_template_profile',
    }


def _json_safe_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe_payload(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe_payload(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe_payload(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _call_structured_llm_json(
    *,
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.2,
    max_tokens: int = 2000,
) -> tuple[dict[str, Any], dict[str, Any]]:
    result = call_chat_completions(
        messages=[
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': user_prompt},
        ],
        temperature=temperature,
        max_tokens=max_tokens,
    )
    parsed = _extract_json_object(result.content)
    if not parsed:
        raise ValueError('llm returned non-json content')
    return parsed, {'usage': result.usage, 'raw': result.raw}


def _call_document_parse_llm(
    *,
    task: Task,
    source_file: File,
    parsed: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    system_prompt = (
        'You are a DocumentParseAgent for PDF-to-PPT workflows. '
        'Return JSON only with keys: sections, key_facts, evidence_spans, doc_summary.'
    )
    user_prompt = _json_safe_payload(
        {
            'task_no': task.task_no,
            'detail_level': task.detail_level,
            'user_prompt': task.user_prompt or '',
            'source_file': {
                'filename': source_file.filename,
                'ext': source_file.ext,
                'size': source_file.file_size,
            },
            'parsed_snapshot': {
                'sections': (parsed.get('sections') or [])[:20],
                'key_facts': (parsed.get('key_facts') or [])[:20],
                'evidence_spans': (parsed.get('evidence_spans') or [])[:20],
                'images_count': len(parsed.get('images') or []),
                'tables_count': len(parsed.get('tables') or []),
            },
            'instructions': [
                'Keep sections in reading order.',
                'Keep key_facts concise and business-oriented.',
                'Evidence text should be short and attributable with page number.',
            ],
        }
    )
    return _call_structured_llm_json(
        system_prompt=system_prompt,
        user_prompt=json.dumps(user_prompt, ensure_ascii=False),
        temperature=0.1,
        max_tokens=1600,
    )


def _call_rag_retrieve_llm(
    *,
    task: Task,
    source_excerpt: str,
    fallback_keywords: list[str],
    rule_query: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    system_prompt = (
        'You are a RagRetrieveAgent. '
        'Return JSON only with keys: query, topic_weights, reasoning.'
    )
    user_prompt = _json_safe_payload(
        {
            'task_no': task.task_no,
            'detail_level': task.detail_level,
            'rag_enabled': bool(task.rag_enabled),
            'user_prompt': task.user_prompt or '',
            'source_excerpt': source_excerpt,
            'fallback_keywords': fallback_keywords[:12],
            'rule_query': rule_query,
            'instructions': [
                'Rewrite retrieval query for precision and coverage.',
                'Return 2-8 topic_weights in range [0,1].',
                'If user_prompt is empty, infer themes from source excerpt and keywords.',
            ],
        }
    )
    return _call_structured_llm_json(
        system_prompt=system_prompt,
        user_prompt=json.dumps(user_prompt, ensure_ascii=False),
        temperature=0.1,
        max_tokens=800,
    )


def _normalize_llm_page_suggestions(raw_pages: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_pages, list):
        return []

    normalized: list[dict[str, Any]] = []
    for raw in raw_pages:
        if not isinstance(raw, dict):
            continue
        try:
            page_no = int(raw.get('page_no') or raw.get('page') or 0)
        except Exception:
            page_no = 0
        if page_no <= 0:
            continue

        suggestion: dict[str, Any] = {'page_no': page_no}
        page_function = _coerce_text(raw.get('page_function') or '').lower()
        if page_function:
            suggestion['page_function'] = page_function

        reason = _coerce_text(raw.get('reason') or raw.get('page_function_reason') or raw.get('analysis'))
        if reason:
            suggestion['reason'] = reason

        confidence = raw.get('confidence')
        if isinstance(confidence, (int, float)):
            suggestion['confidence'] = float(confidence)

        layout_suggestions: dict[str, Any] = {}
        for candidate in (raw.get('layout_suggestions'), raw.get('layout')):
            if isinstance(candidate, dict):
                layout_suggestions.update(candidate)
        for key in ('density_hint', 'title_style', 'columns', 'text_alignment', 'max_bullets', 'layout_strategy'):
            if key in raw and raw.get(key) is not None:
                layout_suggestions[key] = raw.get(key)
        if layout_suggestions:
            suggestion['layout_suggestions'] = layout_suggestions

        style_suggestions: dict[str, Any] = {}
        for candidate in (raw.get('style_suggestions'), raw.get('style')):
            if isinstance(candidate, dict):
                style_suggestions.update(candidate)
        for key in ('accent_strategy', 'primary_color', 'accent_color', 'background_color', 'text_color'):
            if key in raw and raw.get(key) is not None:
                style_suggestions[key] = raw.get(key)
        if style_suggestions:
            suggestion['style_suggestions'] = style_suggestions

        normalized.append(suggestion)

    return normalized


def _normalize_llm_slot_overrides(raw_overrides: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_overrides, list):
        return []

    normalized: list[dict[str, Any]] = []
    for raw in raw_overrides:
        if not isinstance(raw, dict):
            continue
        try:
            slide_no = int(raw.get('slide_no') or raw.get('page_no') or raw.get('slide') or 0)
        except Exception:
            slide_no = 0
        if slide_no <= 0:
            continue

        slot_key = _coerce_text(raw.get('slot_key') or raw.get('slot') or raw.get('name'))
        slot_type = _coerce_text(raw.get('slot_type') or raw.get('type') or '').lower()
        if not slot_key:
            continue
        if slot_type not in {'text', 'image', 'table'}:
            slot_type = 'text'

        override: dict[str, Any] = {
            'slide_no': slide_no,
            'slot_key': slot_key,
            'slot_type': slot_type,
        }
        content_source = _coerce_text(raw.get('content_source') or raw.get('source'))
        if content_source:
            override['content_source'] = content_source
        fill_status = _coerce_text(raw.get('fill_status'))
        if fill_status:
            override['fill_status'] = fill_status
        quality_score = raw.get('quality_score')
        if isinstance(quality_score, (int, float)):
            override['quality_score'] = float(quality_score)

        planned_value = raw.get('planned_value')
        if planned_value is None:
            planned_value = raw.get('hint')
        if planned_value is None:
            planned_value = raw.get('value')
        if planned_value is not None:
            override['planned_value'] = planned_value

        normalized.append(override)

    return normalized


def _normalize_llm_fix_ops(raw_fix_ops: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_fix_ops, list):
        return []

    normalized: list[dict[str, Any]] = []
    for raw in raw_fix_ops:
        if not isinstance(raw, dict):
            continue
        op = _coerce_text(raw.get('op') or raw.get('action') or raw.get('type'))
        if not op:
            continue
        fix_op: dict[str, Any] = {'op': op}
        for key in ('target', 'reason', 'reason_code', 'scope', 'page_no', 'priority'):
            value = raw.get(key)
            if value not in (None, '', []):
                fix_op[key] = value
        normalized.append(fix_op)
    return normalized


def _call_map_slots_llm(
    *,
    task: Task,
    slide_plan: list[dict[str, Any]],
    template_pages: list[dict[str, Any]],
    parsed_images: list[dict[str, Any]],
    parsed_tables: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    system_prompt = (
        'You are a presentation slot-mapping agent. '
        'Return JSON only with page_suggestions and slot_fill_overrides.'
    )
    user_prompt = _json_safe_payload(
        {
            'detail_level': task.detail_level,
            'task_no': task.task_no,
            'slide_plan': slide_plan,
            'template_pages': [
                {
                    'page_no': page.get('page_no'),
                    'page_function': page.get('page_function'),
                    'cluster_label': page.get('cluster_label'),
                    'layout_schema_json': page.get('layout_schema_json'),
                    'style_tokens_json': page.get('style_tokens_json'),
                }
                for page in template_pages[:24]
            ],
            'parsed_image_samples': [
                {
                    'page_no': image.get('page_no'),
                    'image_path': image.get('image_path') or image.get('path'),
                    'caption': image.get('caption'),
                    'alt_text': image.get('alt_text'),
                }
                for image in parsed_images[:8]
            ],
            'parsed_table_samples': [
                {
                    'page_no': table.get('page_no'),
                    'title': table.get('title'),
                    'headers': table.get('headers'),
                    'rows': table.get('rows')[:3] if isinstance(table.get('rows'), list) else table.get('rows'),
                }
                for table in parsed_tables[:8]
            ],
            'instructions': [
                'Prefer same-type template pages first, then similar pages.',
                'Return page_suggestions in the shape accepted by template suggestion overlay.',
                'Return slot_fill_overrides only when you can improve a text/image/table slot with concrete hints.',
            ],
        }
    )
    payload, meta = _call_structured_llm_json(system_prompt=system_prompt, user_prompt=json.dumps(user_prompt, ensure_ascii=False))
    return payload, meta


def _call_generate_slides_llm(
    *,
    task: Task,
    mapped_slide_plan: list[dict[str, Any]],
    slot_fill_plan: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    system_prompt = (
        'You are a slide generation agent. '
        'Return JSON only with edit_ops and optionally mapped_slide_plan.'
    )
    user_prompt = _json_safe_payload(
        {
            'detail_level': task.detail_level,
            'task_no': task.task_no,
            'mapped_slide_plan': mapped_slide_plan,
            'slot_fill_plan': slot_fill_plan[:120],
            'instructions': [
                'Create concrete edit_ops for title, bullets, tables and images.',
                'Keep the structure editable and template aligned.',
                'If you emit mapped_slide_plan, keep it slide-by-slide aligned to the input plan.',
            ],
        }
    )
    payload, meta = _call_structured_llm_json(system_prompt=system_prompt, user_prompt=json.dumps(user_prompt, ensure_ascii=False), max_tokens=2800)
    return payload, meta


def _call_self_correct_llm(
    *,
    task: Task,
    slide_plan: list[dict[str, Any]],
    slot_fill_plan: list[dict[str, Any]],
    edit_ops: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    system_prompt = (
        'You are a layout self-correction agent. '
        'Return JSON only with fix_ops, retry_recommended and quality_report.'
    )
    user_prompt = _json_safe_payload(
        {
            'detail_level': task.detail_level,
            'task_no': task.task_no,
            'slide_plan': slide_plan,
            'slot_fill_plan': slot_fill_plan[:120],
            'edit_ops': edit_ops[:120],
            'instructions': [
                'Detect overflow, collision, alignment, and density problems.',
                'Prefer conservative fixes that preserve editability.',
                'Provide a quality_report with overflow, collision and risk_score when possible.',
            ],
        }
    )
    payload, meta = _call_structured_llm_json(system_prompt=system_prompt, user_prompt=json.dumps(user_prompt, ensure_ascii=False), max_tokens=2200)
    return payload, meta


def _pop_best_asset_for_slide(
    assets: list[dict[str, Any]],
    *,
    slide_no: int,
    state: dict[str, Any],
    key: str,
) -> dict[str, Any] | None:
    if not assets:
        return None

    used_key = f'{key}_used'
    used_indices = state.get(used_key)
    if not isinstance(used_indices, list):
        used_indices = []
        state[used_key] = used_indices
    consumed = {int(item) for item in used_indices if isinstance(item, int)}

    candidate_indices = [idx for idx in range(len(assets)) if idx not in consumed]
    if not candidate_indices:
        return None

    exact_page = [idx for idx in candidate_indices if int(assets[idx].get('page_no') or 0) == slide_no]
    if exact_page:
        chosen = exact_page[0]
        used_indices.append(chosen)
        state[key] = chosen + 1
        return assets[chosen]

    chosen = min(
        candidate_indices,
        key=lambda idx: (
            abs(int(assets[idx].get('page_no') or slide_no) - slide_no),
            idx,
        ),
    )
    used_indices.append(chosen)
    state[key] = chosen + 1
    return assets[chosen]


def _build_slot_fill_value(
    slide: dict[str, Any],
    slot: dict[str, Any],
    *,
    parsed_images: list[dict[str, Any]] | None = None,
    parsed_tables: list[dict[str, Any]] | None = None,
    asset_cursor_state: dict[str, Any] | None = None,
) -> Any:
    title = str(slide.get('title') or '').strip()
    bullets = _to_bullet_list(slide.get('bullets'))
    slot_type = str(slot.get('slot_type') or 'text')
    slot_role = str(slot.get('slot_role') or '')
    slide_no = int(slide.get('page_no') or slide.get('slide_no') or 0)
    cursor_state = asset_cursor_state if isinstance(asset_cursor_state, dict) else {}

    if slot_type == 'image':
        image_asset = _pop_best_asset_for_slide(
            parsed_images or [],
            slide_no=slide_no,
            state=cursor_state,
            key='image_index',
        )
        if image_asset:
            image_path = _coerce_text(image_asset.get('image_path') or image_asset.get('path'))
            caption = _coerce_text(image_asset.get('caption') or image_asset.get('alt_text'), default=title or f'Image S{slide_no}')
            return {
                'source': 'doc_image',
                'hint': {
                    'image_path': image_path,
                    'path': image_path,
                    'caption': caption,
                    'alt_text': _coerce_text(image_asset.get('alt_text'), default=caption),
                    'source': 'pdf_native_image',
                },
            }
        return {'source': 'doc_image', 'hint': title or f"image_for_slide_{slide.get('page_no')}"}
    if slot_type == 'table':
        table_asset = _pop_best_asset_for_slide(
            parsed_tables or [],
            slide_no=slide_no,
            state=cursor_state,
            key='table_index',
        )
        if table_asset:
            return {
                'source': 'doc_table',
                'hint': {
                    'title': _coerce_text(table_asset.get('title'), default=f'Table S{slide_no}'),
                    'headers': table_asset.get('headers') or ['Item', 'Value'],
                    'rows': table_asset.get('rows') or [[title or f'Slide {slide_no}', '']],
                    'source': 'pdf_native_table',
                },
            }
        return {'source': 'doc_table', 'hint': bullets[:4] or [title]}
    if slot_role == 'title':
        return title
    if slot_role == 'subtitle':
        return bullets[0] if bullets else title
    return bullets[:6] if bullets else [title or f"content_for_slide_{slide.get('page_no')}"]


def _extract_slot_hint(slot_fill_item: dict[str, Any]) -> Any:
    fill_json = slot_fill_item.get('fill_json')
    if not isinstance(fill_json, dict):
        return None
    planned_value = fill_json.get('planned_value')
    if isinstance(planned_value, dict):
        for key in ('hint', 'value', 'text'):
            value = planned_value.get(key)
            if value not in (None, '', []):
                return value
        return planned_value
    return planned_value


def _slot_has_real_image_asset(planned_value: Any) -> bool:
    if not isinstance(planned_value, dict):
        return False
    hint = planned_value.get('hint')
    if isinstance(hint, dict):
        image_path = _coerce_text(hint.get('image_path') or hint.get('path'))
        return bool(image_path)
    return False


def _slot_has_structured_table_asset(planned_value: Any) -> bool:
    if not isinstance(planned_value, dict):
        return False
    hint = planned_value.get('hint')
    if isinstance(hint, dict):
        headers = hint.get('headers')
        rows = hint.get('rows')
        has_headers = isinstance(headers, list) and len(headers) > 0
        has_rows = isinstance(rows, list) and len(rows) > 0
        return has_headers and has_rows
    return False


def _determine_slot_fill_status(
    *,
    slot_type: str,
    planned_value: Any,
    template_mapping_fallback_level: int,
) -> tuple[str, float]:
    slot = (slot_type or 'text').lower()
    mapping_fallback = int(template_mapping_fallback_level)

    if slot == 'image':
        if _slot_has_real_image_asset(planned_value):
            return ('success', 0.95 if mapping_fallback == 0 else 0.86)
        return ('fallback', 0.74)

    if slot == 'table':
        if _slot_has_structured_table_asset(planned_value):
            return ('success', 0.95 if mapping_fallback == 0 else 0.86)
        return ('fallback', 0.72)

    text_ok = bool(_coerce_text(planned_value))
    if text_ok:
        return ('success', 0.95 if mapping_fallback == 0 else 0.84)
    return ('fallback', 0.7)


def _coerce_text(value: Any, default: str = '') -> str:
    if value is None:
        return default
    if isinstance(value, str):
        text = value.strip()
        return text or default
    return str(value).strip() or default


def _normalize_table_rows_from_list_dict(hint: list[dict[str, Any]]) -> tuple[list[str], list[list[str]]]:
    headers: list[str] = []
    rows: list[list[str]] = []
    if not hint:
        return headers, rows

    for row_idx, row in enumerate(hint, start=1):
        if not isinstance(row, dict):
            continue
        if not headers:
            headers = [key for key in row.keys() if _coerce_text(key)]
        row_values = [_coerce_text(value, default='-') for value in row.values()]
        if row_values:
            rows.append(row_values)

    if headers and rows:
        normalized_rows: list[list[str]] = []
        for row in rows[:6]:
            normalized_rows.append(row[: len(headers)] + [''] * max(0, len(headers) - len(row)))
        return headers[:6], normalized_rows

    return headers, rows


def _build_table_asset(slot_fill_item: dict[str, Any]) -> dict[str, Any]:
    slot_key = str(slot_fill_item.get('slot_key') or 'table')
    hint = _extract_slot_hint(slot_fill_item)
    headers: list[str] = ['Item', 'Value']
    rows: list[list[str]] = []

    if isinstance(hint, dict):
        raw_headers = hint.get('headers')
        raw_rows = hint.get('rows')
        if isinstance(raw_headers, list):
            normalized_headers = [_coerce_text(value) for value in raw_headers if _coerce_text(value)]
            if normalized_headers:
                headers = normalized_headers[:6]
        if isinstance(raw_rows, list):
            for row in raw_rows[:6]:
                if isinstance(row, dict):
                    row_values = [_coerce_text(row.get(header), default='-') for header in headers]
                elif isinstance(row, (list, tuple)):
                    row_values = [_coerce_text(value, default='-') for value in row]
                else:
                    row_values = [_coerce_text(row, default='-')]
                if row_values:
                    rows.append(row_values)
        if not rows:
            for idx, (key, value) in enumerate(list(hint.items())[:6], start=1):
                if key in {'headers', 'rows'}:
                    continue
                rows.append([_coerce_text(key) or f'Field {idx}', _coerce_text(value, default='-')])
    elif isinstance(hint, list):
        if hint and all(isinstance(item, dict) for item in hint):
            headers, rows = _normalize_table_rows_from_list_dict(hint)  # type: ignore[arg-type]
        else:
            for idx, item in enumerate(hint[:6], start=1):
                rows.append([str(idx), _coerce_text(item, default='-')])
    else:
        rows.append([slot_key.replace('_', ' ').title(), _coerce_text(hint, default='Table placeholder')])

    return {
        'slot_key': slot_key,
        'title': slot_key.replace('_', ' ').title(),
        'headers': headers or ['Item', 'Value'],
        'rows': rows,
        'source': str(slot_fill_item.get('content_source') or 'doc_table'),
        'slot_type': 'table',
    }


def _build_image_asset(slot_fill_item: dict[str, Any]) -> dict[str, Any]:
    slot_key = str(slot_fill_item.get('slot_key') or 'image')
    hint = _extract_slot_hint(slot_fill_item)
    image_path = ''
    caption = ''
    alt_text = ''
    source = str(slot_fill_item.get('content_source') or 'doc_image')
    if isinstance(hint, dict):
        image_path = _coerce_text(hint.get('image_path') or hint.get('path'))
        caption_hint = _coerce_text(hint.get('caption') or hint.get('title'))
        alt_text_hint = _coerce_text(hint.get('alt_text'))
        if image_path or caption_hint or alt_text_hint:
            caption = caption_hint or slot_key.replace('_', ' ').title()
            alt_text = alt_text_hint or caption
        source = _coerce_text(hint.get('source') or hint.get('content_source') or source, default=source)
    elif hint not in (None, ''):
        caption = _coerce_text(hint, default=slot_key.replace('_', ' ').title())
        alt_text = caption
    return {
        'slot_key': slot_key,
        'caption': caption,
        'alt_text': alt_text,
        'image_path': image_path,
        'source': source,
        'slot_type': 'image',
    }


def _inject_slide_assets(
    mapped_slide_plan: list[dict[str, Any]],
    slot_fill_plan: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped_assets: dict[int, dict[str, list[dict[str, Any]]]] = {}
    for item in slot_fill_plan:
        slot_type = str(item.get('slot_type') or '').lower()
        if slot_type not in {'table', 'image'}:
            continue
        slide_no = int(item.get('slide_no') or 0)
        if slide_no <= 0:
            continue
        bucket = grouped_assets.setdefault(slide_no, {'tables': [], 'images': []})
        if slot_type == 'table':
            table_asset = _build_table_asset(item)
            if table_asset.get('rows'):
                bucket['tables'].append(table_asset)
        else:
            image_asset = _build_image_asset(item)
            if image_asset.get('caption') or image_asset.get('image_path'):
                bucket['images'].append(image_asset)

    enriched: list[dict[str, Any]] = []
    for idx, slide in enumerate(mapped_slide_plan, start=1):
        slide_no = int(slide.get('page_no') or slide.get('slide_no') or idx)
        merged = dict(slide)
        asset_bucket = grouped_assets.get(slide_no)
        if asset_bucket:
            if asset_bucket['tables']:
                merged.setdefault('tables', [])
                merged['tables'].extend(asset_bucket['tables'])
            if asset_bucket['images']:
                merged.setdefault('images', [])
                merged['images'].extend(asset_bucket['images'])
        enriched.append(merged)
    return enriched


def _persist_task_page_mappings(
    db: Session,
    *,
    task_id: int,
    attempt_no: int,
    page_mappings: list[dict[str, Any]],
) -> None:
    db.execute(
        delete(TaskPageMapping).where(
            TaskPageMapping.task_id == task_id,
            TaskPageMapping.attempt_no == attempt_no,
        )
    )
    for item in page_mappings:
        db.add(
            TaskPageMapping(
                task_id=task_id,
                attempt_no=attempt_no,
                slide_no=int(item['slide_no']),
                page_function=str(item['page_function']),
                template_page_no=int(item['template_page_no']),
                mapping_score=float(item['mapping_score']),
                fallback_level=int(item['fallback_level']),
                mapping_json=item['mapping_json'],
            )
        )
    db.flush()


def _persist_task_slot_fillings(
    db: Session,
    *,
    task_id: int,
    attempt_no: int,
    slot_fill_plan: list[dict[str, Any]],
) -> None:
    db.execute(
        delete(TaskSlotFilling).where(
            TaskSlotFilling.task_id == task_id,
            TaskSlotFilling.attempt_no == attempt_no,
        )
    )
    for item in slot_fill_plan:
        db.add(
            TaskSlotFilling(
                task_id=task_id,
                attempt_no=attempt_no,
                slide_no=int(item['slide_no']),
                slot_key=str(item['slot_key']),
                slot_type=str(item['slot_type']),
                content_source=str(item['content_source']),
                fill_status=str(item['fill_status']),
                quality_score=float(item['quality_score']) if item.get('quality_score') is not None else None,
                overflow_flag=1 if item.get('overflow_flag') else 0,
                overlap_flag=1 if item.get('overlap_flag') else 0,
                fill_json=item['fill_json'],
            )
        )
    db.flush()


def _build_map_slots_step_output(
    db: Session,
    task: Task,
    *,
    step_outputs: dict[str, dict[str, Any]],
    template_profile_id: int | None,
    attempt_no: int,
) -> dict[str, Any]:
    plan_output = step_outputs.get(TaskStepCode.PLAN_SLIDES) or {}
    asset_output = step_outputs.get(TaskStepCode.ASSETIZE_TEMPLATE) or {}
    parse_output = step_outputs.get(TaskStepCode.PARSE_PDF) or {}
    slide_plan = plan_output.get('slide_plan') or []
    if not slide_plan:
        slide_plan = (_mock_step_output(task, TaskStepCode.PLAN_SLIDES)).get('slide_plan') or []

    template_pages = asset_output.get('asset_pages') or []
    if not template_pages and template_profile_id:
        rows = list(
            db.scalars(
                select(TemplatePageSchema)
                .where(TemplatePageSchema.template_profile_id == template_profile_id)
                .order_by(TemplatePageSchema.page_no.asc())
            ).all()
        )
        template_pages = [
            {
                'page_no': row.page_no,
                'cluster_label': row.cluster_label,
                'page_function': row.page_function,
                'layout_schema_json': row.layout_schema_json,
                'style_tokens_json': row.style_tokens_json,
            }
            for row in rows
        ]

    mapping = map_slide_plan_to_template(slide_plan=slide_plan, template_pages=template_pages)
    mapped_slide_plan = mapping.get('mapped_slide_plan') or slide_plan
    parsed_images = parse_output.get('images') or []
    parsed_tables = parse_output.get('tables') or []
    llm_usage: dict[str, Any] | None = None
    llm_error: str | None = None
    llm_suggestions_total = 0
    llm_suggestions_applied = 0
    slot_override_index: dict[tuple[int, str, str], dict[str, Any]] = {}
    try:
        raw_llm_output, llm_meta = _call_map_slots_llm(
            task=task,
            slide_plan=slide_plan,
            template_pages=template_pages,
            parsed_images=parsed_images,
            parsed_tables=parsed_tables,
        )
        llm_suggestions = _normalize_llm_page_suggestions(
            raw_llm_output.get('page_suggestions')
            or raw_llm_output.get('llm_page_suggestions')
            or raw_llm_output.get('pages')
        )
        llm_overlay = apply_template_llm_suggestions(
            mapped_slide_plan=mapped_slide_plan,
            llm_page_suggestions=llm_suggestions,
        )
        mapped_slide_plan = llm_overlay.get('mapped_slide_plan') or mapped_slide_plan
        llm_suggestions_total = int(llm_overlay.get('suggestions_total') or 0)
        llm_suggestions_applied = int(llm_overlay.get('suggestions_applied') or 0)
        slot_overrides = _normalize_llm_slot_overrides(
            raw_llm_output.get('slot_fill_overrides')
            or raw_llm_output.get('slot_overrides')
            or raw_llm_output.get('slot_hints')
        )
        for override in slot_overrides:
            slot_override_index[(int(override['slide_no']), str(override['slot_key']), str(override['slot_type']))] = override
        llm_usage = llm_meta.get('usage') if isinstance(llm_meta, dict) else None
        map_analysis_source = 'map_slots_llm'
    except Exception as exc:
        llm_error = str(exc)
        logger.warning('map slots llm failed, fallback rule-based mapping used: %s', exc)
        map_analysis_source = 'slot_mapping_service'
    asset_cursor_state: dict[str, Any] = {
        'image_index': 0,
        'table_index': 0,
        'image_index_used': [],
        'table_index_used': [],
    }
    page_mappings: list[dict[str, Any]] = []
    slot_fill_plan: list[dict[str, Any]] = []

    for index, slide in enumerate(mapped_slide_plan, start=1):
        slide_no = int(slide.get('page_no') or index)
        template_page_no = int(slide.get('template_page_no') or slide.get('page_no') or index)
        page_function = str(slide.get('page_function') or 'content')
        slot_specs = _normalize_layout_slots(slide.get('layout_schema_json'))
        fallback_level = 0 if template_pages else 3
        mapping_score = 0.95 if template_pages else 0.60

        page_mappings.append(
            {
                'slide_no': slide_no,
                'page_function': page_function,
                'template_page_no': template_page_no,
                'mapping_score': mapping_score,
                'fallback_level': fallback_level,
                'mapping_json': {
                    'title': slide.get('title'),
                    'bullets': slide.get('bullets'),
                    'layout_schema_json': slide.get('layout_schema_json'),
                    'style_tokens_json': slide.get('style_tokens_json'),
                    'mapping_mode': mapping.get('mapping_mode'),
                },
            }
        )

        for slot in slot_specs:
            slot_type = str(slot['slot_type'])
            override = slot_override_index.get((slide_no, str(slot['slot_key']), slot_type))
            if override is not None:
                planned_value = override.get('planned_value')
                if planned_value is None:
                    planned_value = _build_slot_fill_value(
                        slide,
                        slot,
                        parsed_images=parsed_images,
                        parsed_tables=parsed_tables,
                        asset_cursor_state=asset_cursor_state,
                    )
                content_source = str(override.get('content_source') or ('llm_text' if slot_type == 'text' else ('doc_table' if slot_type == 'table' else 'doc_image')))
                fill_status = str(override.get('fill_status') or '')
                quality_score = override.get('quality_score')
            else:
                planned_value = _build_slot_fill_value(
                    slide,
                    slot,
                    parsed_images=parsed_images,
                    parsed_tables=parsed_tables,
                    asset_cursor_state=asset_cursor_state,
                )
                content_source = 'llm_text' if slot_type == 'text' else ('doc_table' if slot_type == 'table' else 'doc_image')
                fill_status = ''
                quality_score = None
            computed_fill_status, computed_quality_score = _determine_slot_fill_status(
                slot_type=slot_type,
                planned_value=planned_value,
                template_mapping_fallback_level=fallback_level,
            )
            if fill_status not in {'success', 'adjusted', 'fallback', 'failed'}:
                fill_status = computed_fill_status
            if quality_score is None:
                quality_score = computed_quality_score
            if fill_status == 'fallback':
                task.fallback_used = 1
            slot_fill_plan.append(
                {
                    'slide_no': slide_no,
                    'slot_key': str(slot['slot_key']),
                    'slot_type': slot_type,
                    'content_source': content_source,
                    'fill_status': fill_status,
                    'quality_score': quality_score,
                    'overflow_flag': False,
                    'overlap_flag': False,
                    'fill_json': {
                        'slot_role': slot['slot_role'],
                        'planned_value': planned_value,
                    },
                }
            )

    _persist_task_page_mappings(db, task_id=task.id, attempt_no=attempt_no, page_mappings=page_mappings)
    _persist_task_slot_fillings(db, task_id=task.id, attempt_no=attempt_no, slot_fill_plan=slot_fill_plan)

    return {
        'attempt_no': attempt_no,
        'mapping_mode': mapping.get('mapping_mode'),
        'template_page_count': mapping.get('template_page_count', len(template_pages)),
        'mapped_slide_plan': mapped_slide_plan,
        'page_mappings': page_mappings,
        'slot_fill_plan': slot_fill_plan,
        'parsed_asset_usage': {
            'images_total': len(parsed_images),
            'tables_total': len(parsed_tables),
            'images_used': len(asset_cursor_state.get('image_index_used') or []),
            'tables_used': len(asset_cursor_state.get('table_index_used') or []),
        },
        'analysis_source': map_analysis_source,
        'llm_usage': llm_usage,
        'llm_error': llm_error,
        'llm_used': map_analysis_source == 'map_slots_llm',
        'fallback_used': map_analysis_source != 'map_slots_llm',
        'fallback_reason': llm_error,
        'llm_suggestions_total': llm_suggestions_total,
        'llm_suggestions_applied': llm_suggestions_applied,
    }


def _normalize_quality_payload(
    task: Task,
    *,
    step_outputs: dict[str, dict[str, Any]],
    self_correct_output: dict[str, Any],
    attempt_no: int,
) -> dict[str, Any]:
    quality_report = self_correct_output.get('quality_report')
    base = quality_report if isinstance(quality_report, dict) else self_correct_output
    map_output = step_outputs.get(TaskStepCode.MAP_SLOTS) or {}
    slot_fill_plan = self_correct_output.get('slot_fill_plan') or map_output.get('slot_fill_plan') or []
    mapped_slide_plan = self_correct_output.get('mapped_slide_plan') or map_output.get('mapped_slide_plan') or []

    slot_counts = {'text': 0, 'image': 0, 'table': 0}
    slot_success = {'text': 0, 'image': 0, 'table': 0}
    fallback_triggered = 0
    fallback_success = 0
    for item in slot_fill_plan:
        slot_type = str(item.get('slot_type') or 'text')
        fill_status = str(item.get('fill_status') or 'failed')
        slot_counts[slot_type] = slot_counts.get(slot_type, 0) + 1
        if fill_status in {'success', 'adjusted', 'fallback'}:
            slot_success[slot_type] = slot_success.get(slot_type, 0) + 1
        if fill_status == 'fallback':
            fallback_triggered += 1
            fallback_success += 1

    def _rate(success: int, total: int) -> float | None:
        return round(success / total, 4) if total else None

    page_metrics = _collect_quality_page_metrics(mapped_slide_plan, slot_fill_plan)
    evaluated_pages = int(task.page_count_final or task.page_count_estimated or page_metrics['evaluated_pages'] or len(mapped_slide_plan) or 0)
    risk_score = float(base.get('risk_score') or 0.0)
    overflow = bool(base.get('overflow'))
    collision = bool(base.get('collision'))
    pass_flag = 0 if overflow or collision else 1
    style_fidelity_score = round(max(0.0, 1.0 - min(risk_score, 1.0)), 4)
    auto_fix_triggered = len(self_correct_output.get('fix_ops') or [])
    auto_fix_success_rate = 1.0 if auto_fix_triggered == 0 or pass_flag else 0.0
    fallback_success_rate = round(fallback_success / fallback_triggered, 4) if fallback_triggered else 0.0

    return {
        'attempt_no': attempt_no,
        'metric_version': 'v1.0',
        'evaluated_pages': evaluated_pages,
        'pass_flag': pass_flag,
        'layout_offset_ratio': 1.0 if overflow else 0.0,
        'box_size_deviation_ratio': 1.0 if collision else 0.0,
        'style_fidelity_score': style_fidelity_score,
        'text_slot_match_rate': _rate(slot_success.get('text', 0), slot_counts.get('text', 0)),
        'image_slot_match_rate': _rate(slot_success.get('image', 0), slot_counts.get('image', 0)),
        'table_slot_match_rate': _rate(slot_success.get('table', 0), slot_counts.get('table', 0)),
        'auto_fix_success_rate': auto_fix_success_rate,
        'fallback_success_rate': fallback_success_rate,
        'editable_text_ratio': page_metrics['editable_text_ratio'],
        'locked_page_ratio': page_metrics['locked_page_ratio'],
        'evaluated_scope_json': {
            'excluded_page_types': page_metrics['excluded_page_types'],
            'metric_version': 'v1.0',
            'calculation': 'editable_text_ratio=editable_pages/evaluated_pages; locked_page_ratio=locked_pages/evaluated_pages',
            'page_diagnostics': page_metrics['page_diagnostics'],
        },
        'report_json': {
            'raw_quality_report': base,
            'fix_ops': self_correct_output.get('fix_ops') or [],
            'page_metrics': page_metrics,
        },
        'quality_score': style_fidelity_score,
        'fallback_used': 1 if fallback_triggered > 0 else 0,
    }


def _upsert_quality_report(
    db: Session,
    *,
    task_id: int,
    quality_payload: dict[str, Any],
) -> TaskQualityReport:
    metric_version = str(quality_payload.get('metric_version') or 'v1.0')
    report = db.scalar(
        select(TaskQualityReport).where(
            TaskQualityReport.task_id == task_id,
            TaskQualityReport.metric_version == metric_version,
        )
    )
    if not report:
        report = TaskQualityReport(task_id=task_id, metric_version=metric_version)
        db.add(report)

    report.evaluated_pages = int(quality_payload.get('evaluated_pages') or 0)
    report.pass_flag = int(quality_payload.get('pass_flag') or 0)
    report.layout_offset_ratio = quality_payload.get('layout_offset_ratio')
    report.box_size_deviation_ratio = quality_payload.get('box_size_deviation_ratio')
    report.style_fidelity_score = quality_payload.get('style_fidelity_score')
    report.text_slot_match_rate = quality_payload.get('text_slot_match_rate')
    report.image_slot_match_rate = quality_payload.get('image_slot_match_rate')
    report.table_slot_match_rate = quality_payload.get('table_slot_match_rate')
    report.auto_fix_success_rate = quality_payload.get('auto_fix_success_rate')
    report.fallback_success_rate = quality_payload.get('fallback_success_rate')
    report.editable_text_ratio = quality_payload.get('editable_text_ratio')
    report.locked_page_ratio = quality_payload.get('locked_page_ratio')
    report.evaluated_scope_json = quality_payload.get('evaluated_scope_json')
    report.report_json = quality_payload.get('report_json')
    db.flush()
    return report


def _steps_for_attempt(task: Task, attempt_no: int) -> list[str]:
    if attempt_no <= 1:
        steps = [
            TaskStepCode.PARSE_PDF,
            TaskStepCode.ANALYZE_TEMPLATE,
            TaskStepCode.ASSETIZE_TEMPLATE,
        ]
        if task.rag_enabled:
            steps.append(TaskStepCode.RAG_RETRIEVE)
        steps.extend(
            [
                TaskStepCode.PLAN_SLIDES,
                TaskStepCode.MAP_SLOTS,
                TaskStepCode.GENERATE_SLIDES,
                TaskStepCode.SELF_CORRECT,
                TaskStepCode.EXPORT_PPT,
            ]
        )
        return steps

    return [
        TaskStepCode.MAP_SLOTS,
        TaskStepCode.GENERATE_SLIDES,
        TaskStepCode.SELF_CORRECT,
        TaskStepCode.EXPORT_PPT,
    ]


def _resolve_step_attempt_no(step_code: str, attempt_no: int) -> int:
    if step_code in _RETRYABLE_ATTEMPT_STEPS:
        return attempt_no
    return 1


def _should_retry_from_self_correct(
    self_correct_output: dict[str, Any],
    quality_payload: dict[str, Any],
) -> tuple[bool, str]:
    retry_recommended = bool(self_correct_output.get('retry_recommended'))
    if retry_recommended:
        return True, 'SELF_CORRECT_RETRY_RECOMMENDED'

    raw_report = self_correct_output.get('quality_report')
    quality_report = raw_report if isinstance(raw_report, dict) else {}
    overflow = bool(quality_report.get('overflow'))
    collision = bool(quality_report.get('collision'))
    if overflow and collision:
        return True, 'QUALITY_GATE_UNRESOLVED'

    # Keep current success behavior stable: only treat both hard signals as retry trigger.
    # A single signal still writes quality report but does not force fallback retry.
    return False, ''


def _upsert_step(
    db: Session,
    *,
    task_id: int,
    step_code: str,
    attempt_no: int = 1,
    step_status: str,
    input_json: dict | None = None,
    output_json: dict | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
) -> TaskStep:
    step_order = _STEP_ORDER[step_code]
    step = db.scalar(
        select(TaskStep).where(
            TaskStep.task_id == task_id,
            TaskStep.step_order == step_order,
            TaskStep.attempt_no == attempt_no,
        )
    )
    if not step:
        step = TaskStep(
            task_id=task_id,
            step_code=step_code,
            step_order=step_order,
            attempt_no=attempt_no,
            step_status=step_status,
        )
        db.add(step)

    step.step_code = step_code
    step.attempt_no = attempt_no
    step.step_status = step_status
    step.input_json = input_json
    step.output_json = output_json
    step.error_code = error_code
    step.error_message = error_message
    step.started_at = started_at
    step.finished_at = finished_at
    if started_at and finished_at:
        step.duration_ms = int((finished_at - started_at).total_seconds() * 1000)
    return step


def _set_task_progress(db: Session, task: Task, *, status: str, step_code: str | None, progress: int, message: str) -> None:
    task.status = status
    task.current_step = step_code
    task.progress = progress

    add_task_event(
        db,
        task_id=task.id,
        event_type=TaskEventType.PROGRESS_UPDATED,
        message=message,
        payload_json={'status': status, 'current_step': step_code, 'progress': progress},
    )

    cache_task_progress(
        task.task_no,
        {'status': status, 'current_step': step_code, 'progress': progress, 'updated_at': datetime.utcnow().isoformat()},
    )
    push_task_event_cache(task.task_no, f"{status}:{step_code or '-'}:{progress}")


def _write_result_file(
    db: Session,
    task: Task,
    filename: str,
    slide_plan: list[dict[str, Any]],
    reference_file: File | None = None,
) -> tuple[File, dict[str, Any]]:
    settings = get_settings()
    rel_path = f'{settings.result_subdir}/{task.user_id}/{task.task_no}/{filename}'
    full_path = settings.storage_root_path / rel_path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    template_path: Path | None = None
    if reference_file and reference_file.storage_path:
        candidate = settings.storage_root_path / reference_file.storage_path
        if candidate.is_file() and candidate.suffix.lower() == '.pptx':
            template_path = candidate

    try:
        render_summary = generate_pptx_from_plan(
            slide_plan=slide_plan,
            output_path=full_path,
            template_path=template_path,
        )
    except PPTXGenerationError as exc:
        raise RuntimeError(f'failed to render pptx: {exc}') from exc

    result_file = File(
        user_id=task.user_id,
        file_role=FileRole.PPT_RESULT,
        storage_provider=settings.storage_provider,
        storage_path=rel_path,
        filename=filename,
        ext='pptx',
        mime_type='application/vnd.openxmlformats-officedocument.presentationml.presentation',
        file_size=full_path.stat().st_size,
        status='uploaded',
    )
    if result_file.retention_expire_at is None:
        result_file.retention_expire_at = _default_retention_expire_at(30)
    db.add(result_file)
    db.flush()
    return result_file, render_summary


def _svg_preview_content(task: Task, page_no: int, total_pages: int, profile_id: int | None) -> str:
    title = f'BetterPPT Preview - {task.task_no}'
    subtitle = f'Page {page_no} / {total_pages}'
    profile_text = f'Profile {profile_id}' if profile_id else 'No profile linked'
    return (
        "<svg xmlns='http://www.w3.org/2000/svg' width='1920' height='1080' viewBox='0 0 1920 1080'>"
        "<defs>"
        "<linearGradient id='bg' x1='0' y1='0' x2='1' y2='1'>"
        "<stop offset='0%' stop-color='#f7faff'/>"
        "<stop offset='100%' stop-color='#e8f0ff'/>"
        "</linearGradient>"
        "</defs>"
        "<rect width='1920' height='1080' fill='url(#bg)'/>"
        "<rect x='120' y='100' width='1680' height='880' rx='36' fill='#ffffff' stroke='#cfd9ea' stroke-width='4'/>"
        f"<text x='180' y='220' font-size='64' font-family='Arial, sans-serif' fill='#1e2b39'>{title}</text>"
        f"<text x='180' y='300' font-size='40' font-family='Arial, sans-serif' fill='#5f6e80'>{subtitle}</text>"
        f"<text x='180' y='380' font-size='34' font-family='Arial, sans-serif' fill='#1b6ef3'>{profile_text}</text>"
        "<rect x='180' y='460' width='520' height='14' rx='7' fill='#1b6ef3'/>"
        "<rect x='180' y='520' width='1120' height='280' rx='28' fill='#f8fbff' stroke='#dce3f2' stroke-width='3'/>"
        f"<text x='220' y='610' font-size='42' font-family='Arial, sans-serif' fill='#233142'>"
        f'Generated preview placeholder for page {page_no}'
        "</text>"
        f"<text x='220' y='700' font-size='30' font-family='Arial, sans-serif' fill='#5f6e80'>"
        f'This SVG is stored on disk and served through the file download endpoint.'
        "</text>"
        "</svg>"
    )


def _write_preview_files(db: Session, task: Task, total_pages: int, profile_id: int | None) -> list[dict[str, Any]]:
    settings = get_settings()
    preview_root_rel = f'{settings.result_subdir}/{task.user_id}/{task.task_no}/preview'
    preview_root = settings.storage_root_path / preview_root_rel
    preview_root.mkdir(parents=True, exist_ok=True)

    db.execute(
        delete(File).where(
            File.user_id == task.user_id,
            File.file_role == FileRole.ASSET_IMAGE,
            File.storage_path.like(f'{preview_root_rel}/%'),
        )
    )

    for stale in preview_root.glob('page_*.svg'):
        stale.unlink(missing_ok=True)

    preview_files: list[dict[str, Any]] = []
    for page_no in range(1, max(1, total_pages) + 1):
        filename = f'page_{page_no:03d}.svg'
        full_path = preview_root / filename
        svg_content = _svg_preview_content(task, page_no=page_no, total_pages=total_pages, profile_id=profile_id)
        full_path.write_text(svg_content, encoding='utf-8')

        preview_file = File(
            user_id=task.user_id,
            file_role=FileRole.ASSET_IMAGE,
            storage_provider=settings.storage_provider,
            storage_path=f'{preview_root_rel}/{filename}',
            filename=filename,
            ext='svg',
            mime_type='image/svg+xml',
            file_size=full_path.stat().st_size,
            status='uploaded',
        )
        if preview_file.retention_expire_at is None:
            preview_file.retention_expire_at = _default_retention_expire_at(30)
        db.add(preview_file)
        db.flush()
        preview_files.append(
            {
                'page_no': page_no,
                'file_id': preview_file.id,
                'filename': preview_file.filename,
                'storage_path': preview_file.storage_path,
                'mime_type': preview_file.mime_type,
            }
        )

    return preview_files


def process_single_task(db: Session, task: Task) -> None:
    if task.status not in {TaskStatus.QUEUED, TaskStatus.RUNNING}:
        return

    if not acquire_task_lock(task.task_no):
        logger.info('skip locked task: %s', task.task_no)
        return

    try:
        task.started_at = task.started_at or datetime.utcnow()
        _set_task_progress(
            db,
            task,
            status=TaskStatus.RUNNING,
            step_code=TaskStepCode.PARSE_PDF,
            progress=6,
            message='task running',
        )
        db.commit()

        reference_file = db.get(File, task.reference_file_id)
        if not reference_file:
            raise RuntimeError(f'reference file missing for task {task.task_no}')
        source_file = db.get(File, task.source_file_id)
        if not source_file:
            raise RuntimeError(f'source file missing for task {task.task_no}')

        step_outputs: dict[str, dict[str, Any]] = {}
        template_profile_id: int | None = task.template_profile_id
        preview_files: list[dict[str, Any]] = []
        attempt_no = 1
        final_attempt_no = 1

        while attempt_no <= _MAX_SELF_CORRECT_ATTEMPTS:
            final_attempt_no = attempt_no
            retry_requested = False

            for step_code in _steps_for_attempt(task, attempt_no):
                step_attempt_no = _resolve_step_attempt_no(step_code, attempt_no)

                db.refresh(task)
                if task.status == TaskStatus.CANCELED:
                    logger.info('task canceled: %s', task.task_no)
                    return

                existing_output = _load_existing_step_output(
                    db,
                    task.id,
                    step_code,
                    attempt_no=step_attempt_no,
                )
                if existing_output is not None:
                    step_outputs[step_code] = existing_output
                    if step_code == TaskStepCode.ANALYZE_TEMPLATE:
                        template_profile_id = int(existing_output.get('profile_id') or template_profile_id or 0) or template_profile_id
                        task.template_profile_id = template_profile_id
                    elif step_code == TaskStepCode.PLAN_SLIDES:
                        task.page_count_estimated = existing_output.get('page_count_estimated', task.page_count_estimated)
                    elif step_code == TaskStepCode.SELF_CORRECT:
                        if existing_output.get('quality_score') is not None:
                            task.quality_score = existing_output.get('quality_score')
                        task.fallback_used = 1 if existing_output.get('fallback_used') else task.fallback_used
                    elif step_code == TaskStepCode.EXPORT_PPT:
                        task.result_file_id = existing_output.get('result_file_id', task.result_file_id)
                        preview_files = existing_output.get('preview_files') or preview_files
                    continue

                progress_start, progress_end = STEP_PROGRESS_RANGE[step_code]
                _set_task_progress(
                    db,
                    task,
                    status=TaskStatus.RUNNING,
                    step_code=step_code,
                    progress=progress_start,
                    message=f'step {step_code} started',
                )
                started_at = datetime.utcnow()
                input_json = _default_step_input(task, step_code)
                input_json['attempt_no'] = step_attempt_no
                if step_code == TaskStepCode.RAG_RETRIEVE:
                    input_json.update(
                        {
                            'source_file_id': source_file.id,
                            'source_filename': source_file.filename,
                            'user_prompt': task.user_prompt,
                            'rag_enabled': bool(task.rag_enabled),
                        }
                    )
                elif step_code == TaskStepCode.ANALYZE_TEMPLATE:
                    input_json.update(
                        {
                            'reference_file_id': reference_file.id,
                            'reference_filename': reference_file.filename,
                        }
                    )
                elif step_code == TaskStepCode.ASSETIZE_TEMPLATE:
                    input_json.update({'template_profile_id': template_profile_id})
                elif step_code == TaskStepCode.MAP_SLOTS:
                    plan_output = step_outputs.get(TaskStepCode.PLAN_SLIDES) or {}
                    input_json.update(
                        {
                            'template_profile_id': template_profile_id,
                            'slide_plan_count': len(plan_output.get('slide_plan') or []),
                        }
                    )
                elif step_code == TaskStepCode.SELF_CORRECT:
                    plan_output = step_outputs.get(TaskStepCode.PLAN_SLIDES) or {}
                    map_output = step_outputs.get(TaskStepCode.MAP_SLOTS) or {}
                    generate_output = step_outputs.get(TaskStepCode.GENERATE_SLIDES) or {}
                    input_json.update(
                        {
                            'slide_plan': plan_output.get('slide_plan', []),
                            'slot_fill_plan': map_output.get('slot_fill_plan', []),
                            'edit_ops': generate_output.get('edit_ops', []),
                        }
                    )

                _upsert_step(
                    db,
                    task_id=task.id,
                    step_code=step_code,
                    attempt_no=step_attempt_no,
                    step_status=TaskStepStatus.RUNNING,
                    input_json=input_json,
                    started_at=started_at,
                )
                db.commit()

                try:
                    output_json = _mock_step_output(task, step_code)
                    if step_code == TaskStepCode.PARSE_PDF:
                        output_json, parse_fallback_used = _build_parse_pdf_step_output(task, source_file)
                        if parse_fallback_used:
                            add_task_event(
                                db,
                                task_id=task.id,
                                event_type=TaskEventType.WARNING,
                                message='parse pdf failed, fallback mock applied',
                                payload_json={'step_code': step_code, 'reason': output_json.get('fallback_reason')},
                            )
                    elif step_code == TaskStepCode.ANALYZE_TEMPLATE:
                        raw_analysis, is_fallback = _analyze_template(db, task, reference_file)
                        if raw_analysis.get('__persisted__'):
                            output_json = {k: v for k, v in raw_analysis.items() if k != '__persisted__'}
                            output_json.setdefault('page_schemas_count', len(output_json.get('pages', [])))
                            output_json['template_parse_source'] = output_json.get('analysis_source') or 'template_service'
                            output_json['analysis_source'] = 'template_service_persisted'
                            template_profile_id = int(output_json.get('profile_id') or 0) or template_profile_id
                        else:
                            analysis = _normalize_template_analysis(task, reference_file, raw_analysis)
                            profile = _upsert_template_profile(db, task, reference_file, analysis)
                            template_profile_id = profile.id
                            output_json = {
                                'profile_id': profile.id,
                                'cluster_count': profile.cluster_count,
                                'total_pages': profile.total_pages,
                                'profile_version': profile.profile_version,
                                'summary_json': profile.summary_json,
                                'analysis_source': 'runner_fallback' if is_fallback else 'template_service',
                            }
                        task.template_profile_id = template_profile_id
                        if is_fallback and not raw_analysis.get('__persisted__'):
                            add_task_event(
                                db,
                                task_id=task.id,
                                event_type=TaskEventType.WARNING,
                                message='template service unavailable, fallback analysis applied',
                                payload_json={'step_code': TaskStepCode.ANALYZE_TEMPLATE},
                            )
                    elif step_code == TaskStepCode.ASSETIZE_TEMPLATE:
                        output_json = _build_assetize_step_output(
                            db,
                            task,
                            template_profile_id=template_profile_id,
                        )
                    elif step_code == TaskStepCode.RAG_RETRIEVE:
                        try:
                            output_json, rag_fallback_used = _build_rag_step_output(task, source_file)
                        except Exception as exc:  # pragma: no cover - defensive branch
                            logger.warning('rag retrieve failed, fallback mock used: %s', exc)
                            output_json = _mock_step_output(task, step_code)
                            rag_fallback_used = True
                            add_task_event(
                                db,
                                task_id=task.id,
                                event_type=TaskEventType.WARNING,
                                message='rag retrieve failed, fallback mock applied',
                                payload_json={'step_code': step_code, 'error': str(exc)},
                            )
                    elif step_code == TaskStepCode.PLAN_SLIDES:
                        output_json, plan_fallback_used = _build_plan_slides_step_output(task, source_file, step_outputs)
                        task.page_count_estimated = output_json.get('page_count_estimated', task.page_count_estimated)
                        if plan_fallback_used:
                            add_task_event(
                                db,
                                task_id=task.id,
                                event_type=TaskEventType.WARNING,
                                message='plan slides llm failed, fallback mock applied',
                                payload_json={'step_code': step_code, 'reason': output_json.get('fallback_reason')},
                            )
                    elif step_code == TaskStepCode.MAP_SLOTS:
                        output_json = _build_map_slots_step_output(
                            db,
                            task,
                            step_outputs=step_outputs,
                            template_profile_id=template_profile_id,
                            attempt_no=step_attempt_no,
                        )
                        fallback_levels = [int(item.get('fallback_level') or 0) for item in output_json.get('page_mappings', [])]
                        max_fallback_level = max(fallback_levels) if fallback_levels else 0
                        if max_fallback_level > 0:
                            task.fallback_used = 1
                            add_task_event(
                                db,
                                task_id=task.id,
                                event_type=TaskEventType.FALLBACK_STARTED,
                                message='fallback flow started from slot mapping',
                                payload_json={
                                    'from_step': TaskStepCode.MAP_SLOTS,
                                    'to_step': TaskStepCode.GENERATE_SLIDES,
                                    'fallback_level': max_fallback_level,
                                    'reason_code': 'TEMPLATE_SLOT_FALLBACK',
                                    'attempt_no': step_attempt_no,
                                },
                            )
                            add_task_event(
                                db,
                                task_id=task.id,
                                event_type=TaskEventType.WARNING,
                                message='slot mapping fallback planned',
                                payload_json={
                                    'from_step': TaskStepCode.MAP_SLOTS,
                                    'to_step': TaskStepCode.GENERATE_SLIDES,
                                    'fallback_level': max_fallback_level,
                                    'reason_code': 'TEMPLATE_SLOT_FALLBACK',
                                    'attempt_no': step_attempt_no,
                                },
                            )
                    elif step_code == TaskStepCode.GENERATE_SLIDES:
                        output_json, generate_fallback_used = _build_generate_slides_step_output(
                            db,
                            task,
                            step_outputs,
                            template_profile_id,
                        )
                        if generate_fallback_used:
                            add_task_event(
                                db,
                                task_id=task.id,
                                event_type=TaskEventType.WARNING,
                                message='generate slides fallback mock applied',
                                payload_json={'step_code': step_code},
                            )
                    elif step_code == TaskStepCode.SELF_CORRECT:
                        output_json = _build_self_correct_step_output(task, step_outputs)
                        quality_payload = _normalize_quality_payload(
                            task,
                            step_outputs=step_outputs,
                            self_correct_output=output_json,
                            attempt_no=step_attempt_no,
                        )
                        _upsert_quality_report(db, task_id=task.id, quality_payload=quality_payload)
                        task.quality_score = quality_payload['quality_score']
                        task.fallback_used = max(task.fallback_used or 0, quality_payload['fallback_used'])
                        output_json['quality_score'] = quality_payload['quality_score']
                        output_json['fallback_used'] = quality_payload['fallback_used']
                        output_json['quality_report'] = {
                            'metric_version': quality_payload['metric_version'],
                            'evaluated_pages': quality_payload['evaluated_pages'],
                            'pass_flag': quality_payload['pass_flag'],
                            'layout_offset_ratio': quality_payload['layout_offset_ratio'],
                            'box_size_deviation_ratio': quality_payload['box_size_deviation_ratio'],
                            'style_fidelity_score': quality_payload['style_fidelity_score'],
                            'text_slot_match_rate': quality_payload['text_slot_match_rate'],
                            'image_slot_match_rate': quality_payload['image_slot_match_rate'],
                            'table_slot_match_rate': quality_payload['table_slot_match_rate'],
                            'auto_fix_success_rate': quality_payload['auto_fix_success_rate'],
                            'fallback_success_rate': quality_payload['fallback_success_rate'],
                            'editable_text_ratio': quality_payload['editable_text_ratio'],
                            'locked_page_ratio': quality_payload['locked_page_ratio'],
                        }
                        should_retry, reason_code = _should_retry_from_self_correct(output_json, quality_payload)
                        if should_retry:
                            raise SelfCorrectRetryRequired(
                                reason_code=reason_code,
                                message='self correct unresolved, fallback retry required',
                            )
                    elif step_code == TaskStepCode.EXPORT_PPT:
                        filename = output_json['filename']
                        plan_output = step_outputs.get(TaskStepCode.PLAN_SLIDES) or {}
                        generate_output = step_outputs.get(TaskStepCode.GENERATE_SLIDES) or {}
                        map_output = step_outputs.get(TaskStepCode.MAP_SLOTS) or {}
                        slide_plan = _resolve_export_slide_plan(step_outputs)
                        if not slide_plan:
                            slide_plan = (_mock_step_output(task, TaskStepCode.PLAN_SLIDES)).get('slide_plan') or []

                        result_file, render_summary = _write_result_file(
                            db,
                            task,
                            filename,
                            slide_plan=slide_plan,
                            reference_file=reference_file,
                        )
                        task.result_file_id = result_file.id
                        task.page_count_final = len(slide_plan) or task.page_count_estimated or 12
                        preview_files = _write_preview_files(
                            db,
                            task,
                            total_pages=task.page_count_final or task.page_count_estimated or 12,
                            profile_id=template_profile_id,
                        )
                        output_json = {
                            'filename': filename,
                            'result_file_id': result_file.id,
                            'preview_files': preview_files,
                            'preview_count': len(preview_files),
                            'render_summary': render_summary,
                        }

                    output_json = _attach_step_audit_fields(step_code, output_json)

                    finished_at = datetime.utcnow()
                    _upsert_step(
                        db,
                        task_id=task.id,
                        step_code=step_code,
                        attempt_no=step_attempt_no,
                        step_status=TaskStepStatus.SUCCEEDED,
                        input_json=input_json,
                        output_json=output_json,
                        started_at=started_at,
                        finished_at=finished_at,
                    )
                    step_outputs[step_code] = output_json
                    _set_task_progress(
                        db,
                        task,
                        status=TaskStatus.RUNNING,
                        step_code=step_code,
                        progress=progress_end,
                        message=f'step {step_code} completed',
                    )
                    db.commit()
                except SelfCorrectRetryRequired as exc:
                    finished_at = datetime.utcnow()
                    next_attempt_no = attempt_no + 1
                    can_retry = next_attempt_no <= _MAX_SELF_CORRECT_ATTEMPTS
                    error_code = '2104' if can_retry else '2105'
                    fallback_level = 2 if can_retry else 3

                    _upsert_step(
                        db,
                        task_id=task.id,
                        step_code=step_code,
                        attempt_no=step_attempt_no,
                        step_status=TaskStepStatus.FAILED,
                        input_json=input_json,
                        error_code=error_code,
                        error_message=str(exc),
                        started_at=started_at,
                        finished_at=finished_at,
                    )

                    payload = {
                        'error_code': error_code,
                        'from_step': TaskStepCode.SELF_CORRECT,
                        'to_step': TaskStepCode.MAP_SLOTS,
                        'fallback_level': fallback_level,
                        'reason_code': exc.reason_code,
                        'attempt_no': next_attempt_no if can_retry else step_attempt_no,
                    }

                    if can_retry:
                        task.fallback_used = 1
                        add_task_event(
                            db,
                            task_id=task.id,
                            event_type=TaskEventType.FALLBACK_STARTED,
                            message='fallback retry started',
                            payload_json=payload,
                        )
                        add_task_event(
                            db,
                            task_id=task.id,
                            event_type=TaskEventType.WARNING,
                            message='layout self-correct failed, fallback retry scheduled',
                            payload_json=payload,
                        )
                        db.commit()
                        retry_requested = True
                        break

                    task.status = TaskStatus.FAILED
                    task.error_code = '2105'
                    task.error_message = str(exc)
                    task.finished_at = datetime.utcnow()
                    add_task_event(
                        db,
                        task_id=task.id,
                        event_type=TaskEventType.FALLBACK_FAILED,
                        message='fallback retry failed',
                        payload_json=payload,
                    )
                    add_task_event(
                        db,
                        task_id=task.id,
                        event_type=TaskEventType.ERROR,
                        message='fallback exhausted after self-correct failure',
                        payload_json=payload,
                    )
                    db.commit()
                    return
                except Exception as exc:  # pragma: no cover - defensive branch
                    finished_at = datetime.utcnow()
                    _upsert_step(
                        db,
                        task_id=task.id,
                        step_code=step_code,
                        attempt_no=step_attempt_no,
                        step_status=TaskStepStatus.FAILED,
                        input_json=input_json,
                        error_code='2001',
                        error_message=str(exc),
                        started_at=started_at,
                        finished_at=finished_at,
                    )
                    task.status = TaskStatus.FAILED
                    task.error_code = '2001'
                    task.error_message = str(exc)
                    task.finished_at = datetime.utcnow()
                    add_task_event(
                        db,
                        task_id=task.id,
                        event_type=TaskEventType.ERROR,
                        message='task failed during step execution',
                        payload_json={'step_code': step_code, 'error': str(exc), 'attempt_no': step_attempt_no},
                    )
                    db.commit()
                    return

            if retry_requested:
                attempt_no += 1
                continue
            break

        task.status = TaskStatus.SUCCEEDED
        task.current_step = None
        task.progress = 100
        task.finished_at = datetime.utcnow()
        add_task_event(
            db,
            task_id=task.id,
            event_type=TaskEventType.STATUS_CHANGED,
            message='task succeeded',
            payload_json={
                'preview_count': len(preview_files),
                'template_profile_id': template_profile_id,
                'attempt_no': final_attempt_no,
                'quality_score': float(task.quality_score) if task.quality_score is not None else None,
                'fallback_used': int(task.fallback_used or 0),
            },
        )
        if int(task.fallback_used or 0) > 0:
            add_task_event(
                db,
                task_id=task.id,
                event_type=TaskEventType.FALLBACK_FINISHED,
                message='fallback flow finished',
                payload_json={
                    'attempt_no': final_attempt_no,
                    'result': 'succeeded',
                },
            )
        cache_task_progress(
            task.task_no,
            {'status': task.status, 'current_step': '', 'progress': 100, 'updated_at': datetime.utcnow().isoformat()},
        )
        db.commit()
        logger.info('task succeeded: %s', task.task_no)
    finally:
        release_task_lock(task.task_no)


def poll_task(db: Session, consumer_name: str) -> tuple[Task | None, str | None]:
    stream_event = claim_task_from_stream(consumer_name=consumer_name, block_ms=2000)
    if stream_event:
        task = db.scalar(select(Task).where(Task.task_no == stream_event.task_no))
        return task, stream_event.message_id

    task = db.scalar(
        select(Task)
        .where(Task.status == TaskStatus.QUEUED)
        .order_by(Task.created_at.asc())
        .limit(1)
    )
    return task, None


def run_worker(*, consumer_name: str, once: bool = False, idle_sleep_seconds: int = 2) -> None:
    while True:
        with SessionLocal() as db:
            task, message_id = poll_task(db, consumer_name)
            if task:
                process_single_task(db, task)
                if message_id:
                    ack_stream_event(message_id)
                if once:
                    return
            else:
                if once:
                    logger.info('no queued task found')
                    return

        time.sleep(idle_sleep_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description='BetterPPT worker runner')
    parser.add_argument('--consumer-name', default='worker-1')
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()

    run_worker(consumer_name=args.consumer_name, once=args.once)


if __name__ == '__main__':
    main()











