from __future__ import annotations

from collections.abc import Iterable
from copy import deepcopy
from typing import Any

from app.core.constants import ALLOWED_DETAIL_LEVELS

_DETAIL_THRESHOLDS = {
    'concise': {
        'overflow': 0.42,
        'collision': 0.28,
        'empty_space': 0.48,
        'alignment_risk': 0.34,
        'density_imbalance': 0.43,
        'title_consistency': 0.38,
    },
    'balanced': {
        'overflow': 0.55,
        'collision': 0.36,
        'empty_space': 0.38,
        'alignment_risk': 0.44,
        'density_imbalance': 0.52,
        'title_consistency': 0.48,
    },
    'detailed': {
        'overflow': 0.68,
        'collision': 0.44,
        'empty_space': 0.28,
        'alignment_risk': 0.54,
        'density_imbalance': 0.61,
        'title_consistency': 0.58,
    },
}


def _normalize_detail_level(detail_level: str | None) -> str:
    normalized = (detail_level or 'balanced').strip().lower()
    return normalized if normalized in ALLOWED_DETAIL_LEVELS else 'balanced'


def _coerce_items(value: Any) -> list[dict]:
    if not value:
        return []
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        return [value]
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes)):
        return [item for item in value if isinstance(item, dict)]
    return []


def _string_length(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, str):
        return len(value.strip())
    if isinstance(value, (int, float, bool)):
        return len(str(value))
    if isinstance(value, dict):
        total = 0
        for item in value.values():
            total += _string_length(item)
        return total
    if isinstance(value, Iterable):
        return sum(_string_length(item) for item in value)
    return len(str(value))


def _collect_slide_metrics(slide_plan: list[dict]) -> dict[str, Any]:
    title_length = 0
    bullet_count = 0
    visual_count = 0
    total_slots = 0
    title_lengths: list[int] = []
    content_density: list[int] = []
    title_presence = 0

    for slide in slide_plan:
        if not isinstance(slide, dict):
            continue

        total_slots += 1
        slide_title_length = _string_length(slide.get('title') or slide.get('heading') or slide.get('page_title'))
        title_length += slide_title_length
        if slide_title_length > 0:
            title_presence += 1
            title_lengths.append(slide_title_length)

        bullets = slide.get('bullets') or slide.get('items') or slide.get('key_points') or []
        slide_bullet_count = 0
        if isinstance(bullets, dict):
            slide_bullet_count += len(bullets)
        elif isinstance(bullets, Iterable) and not isinstance(bullets, (str, bytes)):
            slide_bullet_count += sum(1 for item in bullets if item not in (None, ''))
        elif bullets:
            slide_bullet_count += 1
        bullet_count += slide_bullet_count

        visuals = slide.get('visuals') or slide.get('images') or slide.get('figures') or []
        slide_visual_count = 0
        if isinstance(visuals, dict):
            slide_visual_count += len(visuals)
        elif isinstance(visuals, Iterable) and not isinstance(visuals, (str, bytes)):
            slide_visual_count += sum(1 for item in visuals if item not in (None, ''))
        elif visuals:
            slide_visual_count += 1
        visual_count += slide_visual_count
        content_density.append(slide_bullet_count + slide_visual_count)

    return {
        'slide_count': total_slots,
        'title_length': title_length,
        'bullet_count': bullet_count,
        'visual_count': visual_count,
        'title_lengths': title_lengths,
        'title_presence': title_presence,
        'content_density': content_density,
    }


def _collect_edit_metrics(edit_ops: list[dict]) -> tuple[int, int, int]:
    text_ops = 0
    layout_ops = 0
    total_payload = 0

    for op in edit_ops:
        if not isinstance(op, dict):
            continue

        op_name = str(op.get('op') or op.get('operation') or '').lower()
        total_payload += _string_length(op)

        if any(keyword in op_name for keyword in ('text', 'title', 'copy', 'bullet')):
            text_ops += 1
        if any(keyword in op_name for keyword in ('layout', 'move', 'resize', 'align', 'margin', 'spacing', 'position')):
            layout_ops += 1

    return text_ops, layout_ops, total_payload


def analyze_quality_signals(slide_plan: list[dict] | dict | None, edit_ops: list[dict] | dict | None, detail_level: str) -> dict:
    """
    Rule-based quality scan for the generated slide plan.

    The output is intentionally compact and deterministic so it can be stored in
    task_steps.output_json without any post-processing.
    """

    normalized_detail = _normalize_detail_level(detail_level)
    slides = _coerce_items(slide_plan)
    ops = _coerce_items(edit_ops)

    slide_metrics = _collect_slide_metrics(slides)
    slide_count = slide_metrics['slide_count']
    title_length = slide_metrics['title_length']
    bullet_count = slide_metrics['bullet_count']
    visual_count = slide_metrics['visual_count']
    text_ops, layout_ops, op_payload = _collect_edit_metrics(ops)

    avg_title_length = title_length / slide_count if slide_count else 0.0
    bullets_per_slide = bullet_count / slide_count if slide_count else 0.0
    visuals_per_slide = visual_count / slide_count if slide_count else 0.0
    ops_density = (text_ops + layout_ops) / slide_count if slide_count else 0.0
    title_presence_ratio = slide_metrics['title_presence'] / slide_count if slide_count else 0.0
    title_lengths = slide_metrics['title_lengths']
    title_length_mean = sum(title_lengths) / len(title_lengths) if title_lengths else 0.0
    title_length_span = (max(title_lengths) - min(title_lengths)) if len(title_lengths) > 1 else 0
    title_consistency_span = title_length_span / max(1.0, title_length_mean) if title_length_mean else 0.0

    content_density = slide_metrics['content_density']
    avg_content_density = sum(content_density) / slide_count if slide_count else 0.0
    max_content_density = max(content_density) if content_density else 0
    min_content_density = min(content_density) if content_density else 0
    density_spread = max_content_density - min_content_density

    alignment_risk_score = round(
        min(
            1.0,
            (layout_ops / max(1, slide_count)) * 0.18
            + max(0.0, 1.0 - title_presence_ratio) * 0.38
            + max(0.0, abs(text_ops - visual_count) / max(1, slide_count)) * 0.14,
        ),
        4,
    )
    density_imbalance_score = round(
        min(
            1.0,
            (density_spread / max(1.0, avg_content_density + 1.0)) * 0.7
            + max(0.0, 1.0 - (min_content_density / max(1.0, avg_content_density))) * 0.3,
        ),
        4,
    )
    title_consistency_score = round(
        min(
            1.0,
            title_consistency_span * 0.58 + max(0.0, 1.0 - title_presence_ratio) * 0.42,
        ),
        4,
    )

    overflow_score = round(
        min(
            1.0,
            (avg_title_length / 48.0) * 0.45
            + (bullets_per_slide / 7.0) * 0.35
            + (op_payload / 1600.0) * 0.20,
        ),
        4,
    )
    collision_score = round(
        min(
            1.0,
            (layout_ops / max(1, slide_count)) * 0.22
            + (visuals_per_slide / 3.0) * 0.33
            + (text_ops / max(1, slide_count)) * 0.12,
        ),
        4,
    )
    empty_space_score = round(
        min(
            1.0,
            max(0.0, 1.0 - (bullets_per_slide / 6.0)) * 0.55
            + max(0.0, 1.0 - (ops_density / 4.0)) * 0.45,
        ),
        4,
    )

    thresholds = _DETAIL_THRESHOLDS[normalized_detail]
    overflow = overflow_score >= thresholds['overflow']
    collision = collision_score >= thresholds['collision']
    empty_space = empty_space_score >= thresholds['empty_space']
    alignment_risk = alignment_risk_score >= thresholds['alignment_risk']
    density_imbalance = density_imbalance_score >= thresholds['density_imbalance']
    title_consistency = title_consistency_score >= thresholds['title_consistency']

    risk_score = round(
        min(
            1.0,
            overflow_score * 0.30
            + collision_score * 0.24
            + empty_space_score * 0.16
            + alignment_risk_score * 0.12
            + density_imbalance_score * 0.10
            + title_consistency_score * 0.08,
        ),
        4,
    )

    return {
        'detail_level': normalized_detail,
        'slide_count': slide_count,
        'overflow': overflow,
        'collision': collision,
        'empty_space': empty_space,
        'alignment_risk': alignment_risk,
        'density_imbalance': density_imbalance,
        'title_consistency': title_consistency,
        'risk_score': risk_score,
        'signals': {
            'avg_title_length': round(avg_title_length, 4),
            'bullets_per_slide': round(bullets_per_slide, 4),
            'visuals_per_slide': round(visuals_per_slide, 4),
            'title_presence_ratio': round(title_presence_ratio, 4),
            'avg_content_density': round(avg_content_density, 4),
            'title_length_span': title_length_span,
            'text_ops': text_ops,
            'layout_ops': layout_ops,
            'payload_size': op_payload,
            'thresholds': thresholds,
            'scores': {
                'overflow': overflow_score,
                'collision': collision_score,
                'empty_space': empty_space_score,
                'alignment_risk': alignment_risk_score,
                'density_imbalance': density_imbalance_score,
                'title_consistency': title_consistency_score,
            },
            'flags': {
                'overflow': overflow,
                'collision': collision,
                'empty_space': empty_space,
                'alignment_risk': alignment_risk,
                'density_imbalance': density_imbalance,
                'title_consistency': title_consistency,
            },
        },
    }


def propose_fix_ops(quality_signals: dict, detail_level: str) -> list[dict]:
    """
    Produce deterministic fix operations from quality signals.
    """

    normalized_detail = _normalize_detail_level(detail_level)
    fix_ops: list[dict] = []
    severity = float(quality_signals.get('risk_score') or 0.0)
    signal_flags = {
        'overflow': bool(quality_signals.get('overflow')),
        'collision': bool(quality_signals.get('collision')),
        'empty_space': bool(quality_signals.get('empty_space')),
        'alignment_risk': bool(quality_signals.get('alignment_risk')),
        'density_imbalance': bool(quality_signals.get('density_imbalance')),
        'title_consistency': bool(quality_signals.get('title_consistency')),
    }

    if signal_flags['overflow']:
        fix_ops.append(
            {
                'op': 'reduce_text_density',
                'target': 'content_slides',
                'action': 'trim_bullets',
                'priority': 1,
                'reason': 'overflow_risk',
            }
        )
        fix_ops.append(
            {
                'op': 'shrink_typography',
                'target': 'text_blocks',
                'action': 'reduce_font_size',
                'priority': 2,
                'reason': 'overflow_risk',
            }
        )

    if signal_flags['collision']:
        fix_ops.append(
            {
                'op': 'reflow_layout',
                'target': 'overlapping_regions',
                'action': 'increase_spacing',
                'priority': 1,
                'reason': 'collision_risk',
            }
        )
        fix_ops.append(
            {
                'op': 'separate_visuals_and_text',
                'target': 'mixed_content_slides',
                'action': 'rebalance_columns',
                'priority': 2,
                'reason': 'collision_risk',
            }
        )

    if signal_flags['empty_space']:
        fix_ops.append(
            {
                'op': 'fill_empty_space',
                'target': 'sparse_slides',
                'action': 'add_summary_or_visual',
                'priority': 3,
                'reason': 'empty_space_risk',
            }
        )

    if signal_flags['alignment_risk']:
        fix_ops.append(
            {
                'op': 'normalize_alignment',
                'target': 'misaligned_elements',
                'action': 'snap_to_grid_and_recenter',
                'priority': 2,
                'reason': 'alignment_risk',
            }
        )

    if signal_flags['density_imbalance']:
        fix_ops.append(
            {
                'op': 'rebalance_content_density',
                'target': 'uneven_slides',
                'action': 'redistribute_content_load',
                'priority': 2,
                'reason': 'density_imbalance',
            }
        )

    if signal_flags['title_consistency']:
        fix_ops.append(
            {
                'op': 'standardize_titles',
                'target': 'title_blocks',
                'action': 'normalize_title_length_and_style',
                'priority': 2,
                'reason': 'title_consistency',
            }
        )

    if severity >= 0.75:
        fix_ops.append(
            {
                'op': 'global_rebalance',
                'target': 'deck',
                'action': 'reorder_and_compact',
                'priority': 0,
                'reason': f'high_risk_{normalized_detail}',
            }
        )
    elif severity >= 0.45:
        fix_ops.append(
            {
                'op': 'selective_rebalance',
                'target': 'deck',
                'action': 'compact_problem_slides',
                'priority': 1,
                'reason': f'moderate_risk_{normalized_detail}',
            }
        )

    if not fix_ops:
        fix_ops.append(
            {
                'op': 'noop',
                'target': 'deck',
                'action': 'keep_current_layout',
                'priority': 9,
                'reason': 'quality_acceptable',
            }
        )

    return fix_ops


def _normalize_slide_bullets(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, tuple):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, dict):
        return [str(item).strip() for item in value.values() if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def _crop_title(title: Any, *, max_length: int = 48) -> str:
    normalized = str(title or '').strip()
    if not normalized:
        return ''
    if len(normalized) <= max_length:
        return normalized
    return normalized[: max(0, max_length - 3)].rstrip() + '...'


def _ensure_slide_metadata(slide: dict[str, Any]) -> dict[str, Any]:
    metadata = slide.get('slide_metadata')
    if not isinstance(metadata, dict):
        metadata = {}
        slide['slide_metadata'] = metadata
    return metadata


def apply_fix_ops_to_mapped_slide_plan(
    mapped_slide_plan: list[dict[str, Any]] | None,
    fix_ops: list[dict[str, Any]] | None,
    detail_level: str | None = None,
) -> dict[str, Any]:
    """
    Deterministically apply fix ops to a mapped slide plan.

    This is a pure function: it does not mutate the input plan or fix ops and
    it has no external dependencies.
    """

    normalized_detail = _normalize_detail_level(detail_level)
    plan = deepcopy(mapped_slide_plan or [])
    ops = [op for op in deepcopy(fix_ops or []) if isinstance(op, dict)]

    deck_metadata: dict[str, Any] = {
        'detail_level': normalized_detail,
        'applied_fix_ops': [],
        'rebalance_ops': [],
        'rebalance_mode': None,
    }

    applied_fix_ops: list[dict[str, Any]] = []
    rebalance_ops: list[dict[str, Any]] = []
    rebalance_mode: str | None = None

    for op in ops:
        op_name = str(op.get('op') or op.get('operation') or '').strip().lower()
        if not op_name:
            continue

        normalized_op = {
            'op': op_name,
            'target': str(op.get('target') or ''),
            'action': str(op.get('action') or ''),
            'priority': int(op.get('priority') or 0),
            'reason': str(op.get('reason') or ''),
        }
        applied_fix_ops.append(normalized_op)

        if op_name == 'noop':
            continue

        if op_name in {'global_rebalance', 'selective_rebalance'}:
            rebalance_ops.append(normalized_op)
            if op_name == 'global_rebalance':
                rebalance_mode = 'global'
            elif rebalance_mode is None:
                rebalance_mode = 'selective'
            continue

        for slide in plan:
            if not isinstance(slide, dict):
                continue

            metadata = _ensure_slide_metadata(slide)

            if op_name == 'reduce_text_density':
                bullets = _normalize_slide_bullets(slide.get('bullets'))
                max_bullets = int(op.get('max_bullets') or 4)
                slide['bullets'] = bullets[:max_bullets]
                metadata['text_density_reduced'] = True
                metadata['max_bullets'] = max_bullets
            elif op_name == 'shrink_typography':
                font_scale = float(op.get('font_scale') or 0.92)
                slide['font_scale'] = font_scale
                metadata['font_scale'] = font_scale
            elif op_name == 'reflow_layout':
                adjustments = slide.get('layout_adjustments')
                if not isinstance(adjustments, list):
                    adjustments = [adjustments] if adjustments not in (None, '') else []
                adjustments.append(
                    {
                        'op': op_name,
                        'target': normalized_op['target'],
                        'action': normalized_op['action'],
                        'reason': normalized_op['reason'],
                    }
                )
                slide['layout_adjustments'] = adjustments
                metadata['layout_adjustments'] = adjustments
            elif op_name == 'fill_empty_space':
                bullets = _normalize_slide_bullets(slide.get('bullets'))
                if not bullets:
                    title = _crop_title(slide.get('title') or slide.get('heading') or slide.get('page_title'))
                    bullets = [f'Summary: {title}' if title else 'Summary']
                    slide['bullets'] = bullets
                    metadata['empty_space_filled'] = True
            elif op_name == 'standardize_titles':
                title = _crop_title(slide.get('title') or slide.get('heading') or slide.get('page_title'))
                if title:
                    slide['title'] = title
                metadata['title_standardized'] = True

    deck_metadata['applied_fix_ops'] = applied_fix_ops
    deck_metadata['rebalance_ops'] = rebalance_ops
    deck_metadata['rebalance_mode'] = rebalance_mode

    return {
        'mapped_slide_plan': plan,
        'deck_metadata': deck_metadata,
        'applied_fix_ops': applied_fix_ops,
    }


def run_self_correct(slide_plan: list[dict] | dict | None, edit_ops: list[dict] | dict | None, detail_level: str) -> dict:
    """
    End-to-end self-correction pass for storing directly in task step output.
    """

    quality_signals = analyze_quality_signals(slide_plan, edit_ops, detail_level)
    fix_ops = propose_fix_ops(quality_signals, detail_level)

    quality_report = {
        'detail_level': quality_signals['detail_level'],
        'risk_score': quality_signals['risk_score'],
        'overflow': quality_signals['overflow'],
        'collision': quality_signals['collision'],
        'empty_space': quality_signals['empty_space'],
        'alignment_risk': quality_signals['alignment_risk'],
        'density_imbalance': quality_signals['density_imbalance'],
        'title_consistency': quality_signals['title_consistency'],
        'signals': quality_signals['signals'],
        'recommendation': 'apply_fixes' if fix_ops and fix_ops[0].get('op') != 'noop' else 'no_action',
    }

    return {
        'fix_ops': fix_ops,
        'quality_report': quality_report,
    }
